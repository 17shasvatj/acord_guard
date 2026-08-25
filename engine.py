"""ACORD Guard — governance engine for claims-document generation.

Principle: the model proposes; deterministic code disposes.
Every field must carry a source quote that VERIFIABLY exists in an allowed
source document and contains the value. Missing fields are flagged, never
guessed. Deterministic rules validate the assembled record before any form
is rendered. Output includes a field-by-field audit manifest.
"""
import json, re, subprocess
from dataclasses import dataclass, field as dfield
from datetime import datetime, date
from pathlib import Path

DATA = Path(__file__).parent / "data"

# ----------------------------- Schema ---------------------------------------
# sources: which corpora may legitimately supply each field.
#   call    = the claims call transcript
#   policy  = the declarations page on file
#   book    = the agency book of business / claims log
#   config  = agency configuration (trusted, no span needed)
#   request = supplied explicitly by the requesting user (recorded as such)
SCHEMA = {
    "agency_name":     {"required": True,  "sources": ["config"]},
    "agency_address":  {"required": True,  "sources": ["config"]},
    "agency_phone":    {"required": True,  "sources": ["config"]},
    "insured_name":    {"required": True,  "sources": ["policy", "book", "call"]},
    "insured_address": {"required": True,  "sources": ["policy", "call"]},
    "insured_phone":   {"required": True,  "sources": ["call", "book"]},
    "carrier":         {"required": True,  "sources": ["policy", "book"]},
    "policy_number":   {"required": True,  "sources": ["policy", "book"]},
    "policy_term":     {"required": True,  "sources": ["policy"]},
    "date_of_loss":    {"required": True,  "sources": ["call", "request"]},
    "time_of_loss":    {"required": False, "sources": ["call"]},
    "peril":           {"required": True,  "sources": ["call", "request"]},
    "loss_description":{"required": True,  "sources": ["call"]},
    "deductible":      {"required": True,  "sources": ["policy"]},
    "coverage_a":      {"required": True,  "sources": ["policy"]},
    "prior_claims":    {"required": False, "sources": ["book"]},
    "producer_code":   {"required": False, "sources": ["config"]},   # NOT in config -> must stay blank
    "insured_email":   {"required": False, "sources": ["call", "book"]},
}

# --------------------------- Source loading ---------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()

def load_sources(policy_pdf: str) -> dict:
    """Load every source corpus as verifiable text."""
    out = {}
    out["policy"] = subprocess.run(
        ["pdftotext", "-layout", str(DATA / policy_pdf), "-"],
        capture_output=True, text=True).stdout
    out["call"] = (DATA / "claims_call_transcript.txt").read_text()
    import pandas as pd
    book = pd.read_excel(DATA / "agency_book.xlsx", sheet_name="Book of Business")
    claims = pd.read_excel(DATA / "agency_book.xlsx", sheet_name="Claims Log")
    lines = []
    for _, r in book.iterrows():
        lines.append(" | ".join(f"{c}: {r[c]}" for c in book.columns if str(r[c]) != "nan"))
    for _, r in claims.iterrows():
        lines.append(" | ".join(f"{c}: {r[c]}" for c in claims.columns))
    out["book"] = "\n".join(lines)
    out["config"] = json.dumps(json.loads((DATA / "agency_config.json").read_text()), indent=1)
    return out

# --------------------------- Span verification ------------------------------
@dataclass
class FieldResult:
    name: str
    value: str | None
    source: str | None
    span: str | None
    status: str          # VERIFIED | REJECTED_* | MISSING | CONFIG | REQUEST
    reason: str = ""

def verify_proposals(proposals: list[dict], sources: dict) -> list[FieldResult]:
    """The invention-killer. A proposed field survives only if:
       (1) its source is allowed for that field by the schema,
       (2) its quoted span exists verbatim (whitespace-normalized) in that source,
       (3) the value appears within the quoted span.
       config/request sources are trusted-by-definition but recorded as such."""
    results, seen = [], set()
    cfg = json.loads(sources["config"])   # sources is the single source of truth — no disk I/O here
    for p in proposals:
        name = p["field"]
        seen.add(name)
        spec = SCHEMA.get(name)
        if spec is None:
            results.append(FieldResult(name, None, None, None, "REJECTED_UNKNOWN_FIELD",
                                       "Field not in form schema")); continue
        src, val, span = p.get("source"), p.get("value"), p.get("span")
        if src not in spec["sources"]:
            results.append(FieldResult(name, None, src, span, "REJECTED_SOURCE",
                f"'{src}' is not an allowed source for {name} (allowed: {spec['sources']})")); continue
        if src == "config":
            if p.get("config_key") in cfg and cfg[p["config_key"]] == val:
                results.append(FieldResult(name, val, "config", None, "CONFIG")); continue
            results.append(FieldResult(name, None, "config", None, "REJECTED_NOT_IN_CONFIG",
                f"'{val}' is not present in agency configuration")); continue
        if src == "request":
            results.append(FieldResult(name, val, "request", None, "REQUEST",
                "Supplied by requesting user; recorded as user-provided")); continue
        # Documentary sources require a verifiable span:
        if not span:
            results.append(FieldResult(name, None, src, None, "REJECTED_NO_SPAN",
                "No source quote offered — cannot admit an unevidenced value")); continue
        if _norm(span) not in _norm(sources[src]):
            results.append(FieldResult(name, None, src, span, "REJECTED_SPAN_NOT_FOUND",
                f"Quoted text does not exist in '{src}'")); continue
        if p.get("derived"):
            results.append(FieldResult(name, val, src, span, "VERIFIED_DERIVED",
                "Value is a stated normalization of the verified quote")); continue
        if _norm(str(val)) not in _norm(span):
            results.append(FieldResult(name, None, src, span, "REJECTED_VALUE_NOT_IN_SPAN",
                f"Value '{val}' does not appear in the quoted text — mark derived=true only if it is a normalization")); continue
        results.append(FieldResult(name, val, src, span, "VERIFIED"))
    for name, spec in SCHEMA.items():          # anything never proposed -> MISSING
        if name not in seen:
            results.append(FieldResult(name, None, None, None, "MISSING",
                "Not captured from any allowed source"))
    return results

# --------------------------- Deterministic validation ------------------------
@dataclass
class Rule:
    name: str
    severity: str   # BLOCK | WARN
    passed: bool
    detail: str

def _get(results, name):
    for r in results:
        if r.name == name and r.value is not None:
            return r.value
    return None

def _parse_term(term: str):
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s*to\s*(\d{2}/\d{2}/\d{4})", term or "")
    if not m: return None, None
    f = lambda s: datetime.strptime(s, "%m/%d/%Y").date()
    return f(m.group(1)), f(m.group(2))

def validate(results: list[FieldResult], notice_date: date, policy_text: str) -> list[Rule]:
    rules = []
    term = _get(results, "policy_term")
    dol_raw = _get(results, "date_of_loss")
    dol = None
    if dol_raw:
        for fmt in ("%m/%d/%Y", "%B %d, %Y", "%Y-%m-%d"):
            try: dol = datetime.strptime(dol_raw, fmt).date(); break
            except ValueError: pass
    start, end = _parse_term(term)
    if dol and start and end:
        ok = start <= dol <= end
        rules.append(Rule("loss_date_within_policy_term", "BLOCK", ok,
            f"Loss {dol} vs term {start}–{end}" + ("" if ok else " — LOSS OUTSIDE POLICY PERIOD")))
    else:
        rules.append(Rule("loss_date_within_policy_term", "BLOCK", False,
            "Cannot evaluate — loss date or policy term not verifiably captured"))
    if dol:
        rules.append(Rule("loss_date_not_in_future", "BLOCK", dol <= notice_date,
            f"Loss {dol} vs notice date {notice_date}"))
    desc = (_get(results, "loss_description") or "").casefold()
    surge = any(k in desc for k in ("surge", "flood", "surface water", "rising water", "foot of water"))
    if surge:
        excl = "flood" in policy_text.casefold() and "not covered" in policy_text.casefold()
        rules.append(Rule("excluded_peril_flagged", "WARN", True,
            "Storm surge / surface water reported: policy excludes flood — component must be "
            "listed as EXCLUDED on the notice" if excl else
            "Surge reported; no flood exclusion located on policy"))
    phone = _get(results, "insured_phone")
    if phone:
        digits = re.sub(r"\D", "", phone)
        rules.append(Rule("insured_phone_format", "WARN", len(digits) in (10, 11),
            f"Phone '{phone}'"))
    carrier = (_get(results, "carrier") or "").casefold()
    rules.append(Rule("carrier_matches_policy", "BLOCK",
        bool(carrier) and carrier.split()[0] in policy_text.casefold(),
        f"Carrier on notice: '{_get(results, 'carrier')}'"))
    return rules

# --------------------------- Decision ---------------------------------------
def decide(results, rules):
    # A required field is unmet if NO accepted result carries a value for it —
    # whether it was never captured or its proposal was rejected as unevidenced.
    filled = {r.name for r in results if r.value is not None}
    missing_req = [n for n, spec in SCHEMA.items()
                   if spec["required"] and n not in filled]
    rejected = [r for r in results if r.status.startswith("REJECTED")]
    blocks = [r for r in rules if r.severity == "BLOCK" and not r.passed]
    if blocks:     return "BLOCKED", missing_req, rejected, blocks
    if missing_req: return "HOLD_FOR_INFO", missing_req, rejected, blocks
    return "READY_TO_SUBMIT", missing_req, rejected, blocks
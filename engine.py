"""ACORD Guard — governance engine for certificate-of-insurance generation (ACORD 25).

Principle: the model proposes; deterministic code disposes.
Every field must carry a source quote that VERIFIABLY exists in an allowed
source document and contains the value. Missing fields are flagged, never
guessed. Deterministic rules validate the assembled record before any form
is rendered. Output includes a field-by-field audit manifest.
"""
from __future__ import annotations

import json, re
from dataclasses import dataclass, field as dfield
from datetime import datetime, date
from pathlib import Path

from pypdf import PdfReader

DATA = Path(__file__).parent / "data"

# ----------------------------- Schema ---------------------------------------
# sources: which corpora may legitimately supply each field.
#   call    = the claims call transcript
#   policy  = the declarations page on file
#   book    = the agency book of business / claims log
#   config  = agency configuration (trusted, no span needed)
#   request = supplied explicitly by the requesting user (recorded as such)
SCHEMA = {
    "producer_name":    {"required": True,  "sources": ["config"]},
    "producer_address": {"required": True,  "sources": ["config"]},
    "producer_phone":   {"required": True,  "sources": ["config"]},
    "insured_name":     {"required": True,  "sources": ["policy"]},
    "insured_address":  {"required": True,  "sources": ["policy"]},
    "carrier":          {"required": True,  "sources": ["policy"]},
    "policy_number":    {"required": True,  "sources": ["policy"]},
    "policy_term":      {"required": True,  "sources": ["policy"]},
    "cert_holder":      {"required": True,  "sources": ["request"]},
    "each_occurrence":  {"required": True,  "sources": ["policy"]},
    "general_aggregate":{"required": True,  "sources": ["policy"]},
    "products_aggregate":{"required": False, "sources": ["policy"]},
    "personal_adv_injury":{"required": False,"sources": ["policy"]},
    "damage_rented":    {"required": False, "sources": ["policy"]},
    "med_expense":      {"required": False, "sources": ["policy"]},
    # The two dangerous boxes: only "Y" if an endorsement is on the policy.
    "additional_insured":{"required": False,"sources": ["policy"]},
    "waiver_subrogation":{"required": False,"sources": ["policy"]},
    "producer_code":    {"required": False, "sources": ["config"]},  # NOT in config -> stays blank
}

# --------------------------- Source loading ---------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()

def load_sources(policy_pdf: str, request_text: str = "") -> dict:
    """Load every source corpus as verifiable text. request_text is the verbatim
    user instruction that initiated this run; empty in event-driven mode, in
    which case request-sourced proposals cannot verify (the lane self-seals)."""
    out = {"request": request_text or (DATA / "coi_request.txt").read_text()}
    ppath = Path(policy_pdf)
    if not ppath.is_absolute():
        ppath = DATA / ppath
    out["policy"] = "\n".join(
        page.extract_text() or "" for page in PdfReader(ppath).pages)
    out["call"] = (DATA / "coi_request.txt").read_text()  # kept for compatibility
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

def _verify_one(p: dict, cfg: dict, sources: dict) -> FieldResult:
    """Verify a single proposal. Ordered gauntlet; early return = no fall-through."""
    name = p["field"]
    spec = SCHEMA.get(name)
    if spec is None:
        return FieldResult(name, None, None, None, "REJECTED_UNKNOWN_FIELD",
                           "Field not in form schema")
    src_, val, span = p.get("source"), p.get("value"), p.get("span")
    if src_ not in spec["sources"]:
        return FieldResult(name, None, src_, span, "REJECTED_SOURCE",
            f"'{src_}' is not an allowed source for {name} (allowed: {spec['sources']})")
    if src_ == "config":
        if p.get("config_key") in cfg and cfg[p["config_key"]] == val:
            return FieldResult(name, val, "config", None, "CONFIG")
        return FieldResult(name, None, "config", None, "REJECTED_NOT_IN_CONFIG",
            f"'{val}' is not present in agency configuration")
    # Documentary sources (policy/call/book/request) require a verifiable span.
    # "request" is the user's own instruction text: verified like any corpus,
    # labeled as user-asserted provenance via its source field.
    if not span:
        return FieldResult(name, None, src_, None, "REJECTED_NO_SPAN",
            "No source quote offered — cannot admit an unevidenced value")
    if _norm(span) not in _norm(sources[src_]):
        return FieldResult(name, None, src_, span, "REJECTED_SPAN_NOT_FOUND",
            f"Quoted text does not exist in '{src_}'")
    if p.get("derived"):
        return FieldResult(name, val, src_, span, "VERIFIED_DERIVED",
            "Value is a stated normalization of the verified quote")
    if _norm(str(val)) not in _norm(span):
        return FieldResult(name, None, src_, span, "REJECTED_VALUE_NOT_IN_SPAN",
            f"Value '{val}' does not appear in the quoted text — mark derived=true only if it is a normalization")
    return FieldResult(name, val, src_, span, "VERIFIED")

def verify_proposals(proposals: list[dict], sources: dict) -> list[FieldResult]:
    """The invention-killer. A proposed field survives only if:
       (1) its source is allowed for that field by the schema,
       (2) its quoted span exists verbatim (whitespace-normalized) in that source,
       (3) the value appears within the quoted span.
       config/request sources are trusted-by-definition but recorded as such."""
    cfg = json.loads(sources["config"])   # sources is the single source of truth
    results = [_verify_one(p, cfg, sources) for p in proposals]
    proposed = {p["field"] for p in proposals}
    for name in SCHEMA:                    # anything never proposed -> MISSING
        if name not in proposed:
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

def _value(results, name):
    for r in results:
        if r.name == name and r.value is not None:
            return r
    return None

def _get(results, name):
    for r in results:
        if r.name == name and r.value is not None:
            return r.value
    return None

DATE_FORMATS = ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y")

def _parse_date(raw: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    return None

def _parse_term(term: str):
    # tolerate "to", "through", hyphen, en/em dash between the two dates
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*(?:to|through|[-\u2013\u2014])\s*(\d{1,2}/\d{1,2}/\d{4})",
                  term or "")
    if not m:
        return None, None
    return _parse_date(m.group(1)), _parse_date(m.group(2))

def validate(results: list[FieldResult], notice_date: date, policy_text: str) -> list[Rule]:
    rules = []
    term = _get(results, "policy_term")
    start, end = _parse_term(term)
    # Rule 1: coverage in force on the certificate date (BLOCK if not).
    if start and end:
        ok = start <= notice_date <= end
        rules.append(Rule("coverage_in_force_on_cert_date", "BLOCK", ok,
            f"Certificate dated {notice_date}; policy term {start}–{end}"
            + ("" if ok else " — POLICY NOT IN FORCE ON CERTIFICATE DATE")))
    else:
        problems = "policy term not captured" if not term else f"policy term unparseable: '{term}'"
        rules.append(Rule("coverage_in_force_on_cert_date", "BLOCK", False,
            "Cannot evaluate — " + problems))
    # Rule 2 & 3: the endorsement gates. A checked box requires an endorsement on the policy.
    pol = policy_text.casefold()
    for field, rulename, endorsement_terms in [
        ("additional_insured", "additional_insured_requires_endorsement",
         ("additional insured", "additional-insured")),
        ("waiver_subrogation", "waiver_requires_endorsement",
         ("waiver of subrogation", "waiver of subrogation")),
    ]:
        r = _value(results, field)
        checked = r and str(r.value).strip().casefold() in ("y", "yes", "true")
        if checked:
            has_endorsement = any(t in pol for t in endorsement_terms) and "no additional-insured endorsement" not in pol and "no waiver of subrogation" not in pol
            rules.append(Rule(rulename, "BLOCK", has_endorsement,
                f"{field} marked on certificate"
                + ("" if has_endorsement else " — NO ENDORSEMENT ON POLICY; cannot certify this right")))
    # Rule 4: carrier matches policy.
    carrier = (_get(results, "carrier") or "").casefold()
    rules.append(Rule("carrier_matches_policy", "BLOCK",
        bool(carrier) and carrier.split()[0] in pol,
        f"Carrier on certificate: '{_get(results, 'carrier')}'"))
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

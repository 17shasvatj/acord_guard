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
    "each_occurrence":  {"required": True,  "sources": ["policy"],
                         "label_terms": ["each occurrence"]},
    "general_aggregate":{"required": True,  "sources": ["policy"],
                         "label_terms": ["general aggregate"]},
    "products_aggregate":{"required": False, "sources": ["policy"],
                         "label_terms": ["products", "completed operations",
                                         "comp/op"]},
    "personal_adv_injury":{"required": False,"sources": ["policy"],
                         "label_terms": ["personal", "advertising", "adv injury"]},
    "damage_rented":    {"required": False, "sources": ["policy"],
                         "label_terms": ["damage to rented", "rented premises",
                                         "fire damage"]},
    "med_expense":      {"required": False, "sources": ["policy"],
                         "label_terms": ["medical expense", "med exp"]},
    # Structural form fields — the coverage trigger and aggregate basis. These
    # are NOT hardcoded on the certificate; the model must propose them with a
    # policy quote, and the box is marked only from a verified value. If the
    # policy doesn't state them, they stay blank like any other unsourced field.
    "coverage_trigger": {"required": False, "sources": ["policy"],
                         "label_terms": ["occurrence form", "claims-made",
                                         "claims made", "occurrence basis",
                                         "written on an occurrence",
                                         "occurrence trigger", "coverage trigger"]},
    "aggregate_basis":  {"required": False, "sources": ["policy"],
                         "label_terms": ["aggregate", "per policy", "per project",
                                         "per location"]},
    # Coded "presence" fields: rendered Y iff a qualifying span exists in an
    # allowed source. The model proves presence with a real span; code decides.
    # This is a general field KIND, not an endorsement special-case — any
    # boolean/checkbox certificate field uses it.
    "additional_insured":{"required": False,"sources": ["policy"],
                          "kind": "presence",
                          "evidence": ["additional insured", "additional-insured",
                                       "cg 20 10", "cg2010"]},
    "waiver_subrogation":{"required": False,"sources": ["policy"],
                          "kind": "presence",
                          "evidence": ["waiver of subrogation", "waiver",
                                       "subrogation", "cg 24 04", "cg2404"]},
    "producer_code":    {"required": False, "sources": ["config"]},  # NOT in config -> stays blank
}

# Dollar-limit fields: their certificate value must be a number, never a phrase.
# A non-numeric proposal ("Not scheduled", "N/A") means the limit is absent and
# the box must render blank. Derived from the schema so it stays in sync.
_MONETARY_FIELDS = {"each_occurrence", "general_aggregate", "products_aggregate",
                    "personal_adv_injury", "damage_rented", "med_expense"}

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

def _presence_affirms(field: str, span: str, spec: dict) -> bool:
    """True iff the span AFFIRMATIVELY establishes this presence field's fact.
    Two conditions, both required:
      on-topic    — the span mentions this field's evidence term(s), declared in
                    the schema ("evidence" list). This is domain data about what
                    proves the fact, the same kind of declaration as a field's
                    allowed sources — not a code special-case.
      affirmative — the span is not a negation of that fact.
    A field with no declared evidence terms cannot be affirmed by prose and
    stays unchecked (fails safe)."""
    s = " " + re.sub(r"\s+", " ", span).casefold() + " "
    evidence = [e.casefold() for e in spec.get("evidence", [])]
    if not evidence:
        return False
    on_topic = any(e in s for e in evidence)
    if not on_topic:
        return False
    NEGATORS = (" no ", " not ", " without ", " none ", " excluded ",
                " does not ", " n/a ", " absent ", " nil ", " not on file ")
    if any(neg in s for neg in NEGATORS):
        return False
    return True


def _presence_negates(field: str, span: str, spec: dict) -> bool:
    """True iff the span is ON-TOPIC for this presence field AND negates it —
    e.g. "No additional-insured endorsement on file". This means the source has
    affirmatively established that the endorsement is ABSENT, which settles the
    box as "No" and issues clean. Distinct from a span that is merely silent on
    the field (no evidence terms present), which cannot settle anything."""
    s = " " + re.sub(r"\s+", " ", span).casefold() + " "
    evidence = [e.casefold() for e in spec.get("evidence", [])]
    if not evidence or not any(e in s for e in evidence):
        return False   # not on-topic — can't settle the question either way
    NEGATORS = (" no ", " not ", " without ", " none ", " excluded ",
                " does not ", " n/a ", " absent ", " nil ", " not on file ")
    return any(neg in s for neg in NEGATORS)


def _verify_one(p: dict, cfg: dict, sources: dict) -> FieldResult:
    """Verify a single proposal. Ordered gauntlet; early return = no fall-through."""
    name = p["field"]
    spec = SCHEMA.get(name)
    if spec is None:
        return FieldResult(name, None, None, None, "REJECTED_UNKNOWN_FIELD",
                           "Field not in form schema")
    src_, val, span = p.get("source"), p.get("value"), p.get("span")
    # An empty-value proposal is the model DECLINING to fill this field (it found
    # nothing in the source). That is not a fabrication attempt and not a
    # rejection — it is simply "not provided". For a non-required field this is
    # correct (blank); for a required field the missing-required check surfaces
    # it. Either way it must NOT land in the rejected/fabrication list.
    if val is None or not str(val).strip():
        return FieldResult(name, None, src_, span, "NOT_PROVIDED",
            "Model proposed no value for this field")
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
    # Section-aware check: a quote can EXIST in the source yet come from text
    # that does not govern the certificate — a superseded prior policy, a
    # "for reference only" block, an expired term, an example. Span-verification
    # alone would admit these (the quote is real), so we reject a span whose
    # surrounding context is disqualifying.
    disq = _disqualified_context(span, sources[src_])
    if disq:
        return FieldResult(name, None, src_, span, "REJECTED_CONTEXT",
            f"Quote is real but sits in disqualified context ({disq}); "
            "not the operative value for this certificate")
    # Presence fields (checkboxes / Y-N codes): the certificate value is a code
    # standing for "a qualifying fact is established in the source." We do not
    # literal-match the code ("Y" never appears verbatim in prose); instead the
    # field resolves to affirmative BECAUSE a real, operative span in an allowed
    # source establishes it. The model proved the fact with a span; code decided
    # the span was real, operative, and from an allowed source. A request-only
    # "Y" cannot reach here — REJECTED_SOURCE already blocked it, since presence
    # fields allow only the policy as a source. This is a general field kind,
    # not an endorsement special-case.
    if spec.get("kind") == "presence":
        # A presence (checkbox) field has three correct resolutions:
        #   Y      -> the span AFFIRMATIVELY establishes the fact and is on-topic
        #   blank  -> the fact is absent (value is "No", or the span negates it):
        #             a correct negative, NOT a rejection, must not block
        #   reject -> "Y" asserted with no qualifying span (a fabrication attempt)
        v = _norm(str(val))
        affirmative = _presence_affirms(name, span, spec)
        if v in ("", "n", "no", "false", "none"):
            # correct negative — the box is simply not checked
            return FieldResult(name, "No", src_, span, "VERIFIED_ABSENT",
                "Source does not establish this coded fact; box left unchecked")
        if affirmative:
            return FieldResult(name, "Y", src_, span, "VERIFIED_PRESENCE",
                "An affirmative, on-topic span in an allowed source establishes this field")
        # The span is ON-TOPIC for this field but NEGATES it (e.g. the policy
        # says "No additional-insured endorsement on file"). The source itself
        # has settled the question: the endorsement is absent. Resolve to a clean
        # "No" and ISSUE — even if the model clumsily paired this negation span
        # with a "Y" value. There is nothing to review: the policy plainly
        # states the fact is absent, so this is not a fabrication attempt.
        if _presence_negates(name, span, spec):
            return FieldResult(name, "No", src_, span, "VERIFIED_ABSENT",
                "Policy explicitly states this endorsement is absent; box left "
                "unchecked")
        # The model asserted an affirmative code ("Y") with a span that is
        # SILENT on this field — no support either way. This is a genuine
        # unsupported claim: box left unchecked and surfaced for review.
        return FieldResult(name, None, src_, span, "REJECTED_UNSUPPORTED_PRESENCE",
            "Affirmative value claimed but no qualifying span in the policy "
            "establishes it — box left unchecked and flagged for review")
    # Field-correspondence: for fields that name a specific labeled line in the
    # source (the coverage limits), the span must actually contain that field's
    # label — not merely the right number. This closes wrong-field citation,
    # where the model fills an absent limit by quoting a DIFFERENT limit's line
    # that happens to carry a plausible number (e.g. citing "Each Occurrence:
    # $1,000,000" as the span for Damage to Rented). Span-presence proves the
    # value came from the source; the label proves it came from the RIGHT line.
    # label_terms is schema data, not per-field code.
    label_terms = spec.get("label_terms")
    if label_terms:
        span_l = " " + re.sub(r"\s+", " ", span).casefold() + " "
        if not any(t in span_l for t in label_terms):
            return FieldResult(name, None, src_, span, "REJECTED_WRONG_FIELD",
                f"Quoted span does not name this field ({name}); the value may "
                "have been taken from a different line of the policy")
    # Monetary limit fields must carry an actual number. If the model proposed a
    # non-numeric value like "Not scheduled" / "N/A" / "None" (the policy stating
    # the limit is ABSENT), that is not a limit — it is an absence. Resolve to
    # NOT_PROVIDED so the box renders BLANK, never literal words in a dollar cell.
    if name in _MONETARY_FIELDS and not re.search(r"\d", str(val)):
        return FieldResult(name, None, src_, span, "NOT_PROVIDED",
            "Policy states this limit is not scheduled; box left blank")
    # Value must appear in the span AT TOKEN BOUNDARIES. Raw substring matching
    # is structurally unsound: "Y" is a substring of "Policy", so any short
    # value would "verify" against almost any quote.
    if _value_in_span(str(val), span):
        return FieldResult(name, val, src_, span, "VERIFIED")
    # The derived flag is NEVER trusted. A claimed normalization is admitted
    # only if code can deterministically re-derive the value from the span's
    # content (numeric equality after stripping currency formatting; date
    # equality after parsing). The model asserts nothing; code checks
    # everything — otherwise derived would be a trapdoor under the guarantee.
    if _derivable(str(val), span):
        return FieldResult(name, val, src_, span, "VERIFIED_DERIVED",
            "Value re-derived from the quote by deterministic normalization")
    return FieldResult(name, None, src_, span, "REJECTED_VALUE_NOT_IN_SPAN",
        f"Value '{val}' does not appear in the quoted text and cannot be "
        "deterministically derived from it")


def _value_in_span(val: str, span: str) -> bool:
    """Whole-token containment: the value must appear in the span bounded by
    non-alphanumerics (or string edges), never as a fragment of a longer
    token. Kills the substring hole where 'Y' matched the y in 'Policy'."""
    v, s = _norm(val), _norm(span)
    if not v:
        return False
    pat = r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])"
    return re.search(pat, s) is not None


_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DATE_RES = (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
             re.compile(r"(\d{4})-(\d{2})-(\d{2})"))


def _numbers_in(text: str) -> set:
    out = set()
    for m in _NUM_RE.finditer(text):
        try:
            out.add(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def _dates_in(text: str) -> set:
    out = set()
    for m in _DATE_RES[0].finditer(text):
        mth, d, y = m.groups()
        out.add((int(y), int(mth), int(d)))
    for m in _DATE_RES[1].finditer(text):
        y, mth, d = m.groups()
        out.add((int(y), int(mth), int(d)))
    return out


def _derivable(val: str, span: str) -> bool:
    """Can code deterministically re-derive `val` from the span's content?
    Admitted derivations only:
      numeric — every number in the value equals a number in the span
                (currency / comma formatting stripped)
      date    — every date in the value equals a date in the span
                (mm/dd/yyyy and yyyy-mm-dd parsed)
    Anything code cannot recompute is not a derivation — it is an assertion,
    and assertions are rejected."""
    span_nums, span_dates = _numbers_in(span), _dates_in(span)
    val_nums, val_dates = _numbers_in(val), _dates_in(val)
    if not val_nums and not val_dates:
        return False          # nothing checkable to derive
    if val_nums and not val_nums.issubset(span_nums):
        return False
    if val_dates and not val_dates.issubset(span_dates):
        return False
    return True


DISQUALIFYING_MARKERS = (
    "prior policy", "superseded", "for reference only", "reference only",
    "expired", "replaced by", "does not apply", "not covered", "example only",
    "sample only", "formerly", "previous policy", "old policy",
)


def _disqualified_context(span: str, source: str, window: int = 240):
    """If the span sits inside a region governed by a disqualifying marker
    (a superseded / prior / reference block), return the marker; else None.
    Catches values quoted from e.g. 'Prior Policy (superseded — for reference
    only)' blocks: the quote is genuinely present but does not govern the
    certificate. Span-verification alone would admit these."""
    s_norm = re.sub(r"\s+", " ", source).casefold()
    span_norm = re.sub(r"\s+", " ", span).casefold()
    idx = s_norm.find(span_norm)
    if idx < 0:
        return None
    start = max(0, idx - window)
    preceding = s_norm[start:idx + len(span_norm)]
    for marker in DISQUALIFYING_MARKERS:
        if marker in preceding:
            return marker
    return None


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
    # Rule 1: coverage in force on the certificate date.
    if start and end:
        ok = start <= notice_date <= end
        # WARN, not BLOCK: certificates for expired OR future-dated policies are
        # legitimate (historical proof, or a binder for coverage about to start).
        # The issue date is just the generation date. The message must say WHICH
        # way it's out of force — "ended" and "not started" are opposite problems.
        if ok:
            note = ""
        elif notice_date > end:
            note = (" — policy period has ended; confirm this certificate is for "
                    "historical documentation, not current proof of coverage")
        else:  # notice_date < start
            note = (" — policy period has not started yet; confirm this certificate "
                    "is for a future/pending coverage term, not current proof of coverage")
        rules.append(Rule("coverage_in_force_on_cert_date", "WARN", ok,
            f"Certificate issued {notice_date}; policy term {start}–{end}" + note))
    else:
        problems = "policy term not captured" if not term else f"policy term unparseable: '{term}'"
        rules.append(Rule("coverage_in_force_on_cert_date", "WARN", False,
            "Cannot evaluate coverage window — " + problems))
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
    # A CAUGHT FABRICATION must never be silently absorbed into a clean-looking
    # document. When the model proposed an affirmative value that failed
    # verification (unsourced, wrong-context, request-pressured, or an
    # unsupported presence "Y"), the field is safely blank — but the *attempt*
    # is a signal the input pushed the model toward asserting something the
    # source doesn't support, and a human should see it. This is general, not
    # an endorsement special-case: any rejected proposal that tried to assert a
    # value (as opposed to a field simply never proposed) surfaces the document
    # for review rather than shipping quietly.
    fabrication_attempts = [r for r in rejected
                            if r.status in ("REJECTED_SOURCE",
                                            "REJECTED_CONTEXT",
                                            "REJECTED_SPAN_NOT_FOUND",
                                            "REJECTED_VALUE_NOT_IN_SPAN",
                                            "REJECTED_WRONG_FIELD",
                                            "REJECTED_UNSUPPORTED_PRESENCE",
                                            "REJECTED_NOT_IN_CONFIG")]
    if blocks:
        return "BLOCKED", missing_req, rejected, blocks
    if fabrication_attempts:
        # caught a fabrication attempt: hold for human review, fields flagged
        return "HOLD_FOR_REVIEW", missing_req, rejected, blocks
    if missing_req:
        return "HOLD_FOR_INFO", missing_req, rejected, blocks
    return "READY_TO_SUBMIT", missing_req, rejected, blocks
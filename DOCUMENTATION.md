# ACORD Guard — Technical Documentation (v0.1, as built)

This documents exactly what exists in this repository today: what each file does,
the data contracts between them, what has been tested and proven, what is a
deliberate seam for later work, and the known limitations. Nothing described
here is aspirational — if it isn't built, it's listed under "Seams" or
"Limitations."

---

## 0. The mental model (read this first)

The entire system is one sentence: **the model proposes; deterministic code
disposes.**

An LLM is never allowed to write a value onto a document. It may only *propose*
`{field, value, source, span}` — "I think the deductible is $8,200, and here is
the exact quote from the policy that says so." Deterministic code then:

1. **Verifies** the quote actually exists in that source and contains the value
   (the span verifier — the invention-killer).
2. **Validates** the assembled record against insurance rules (loss date inside
   the policy term, excluded perils flagged, formats).
3. **Decides**: READY_TO_SUBMIT, HOLD_FOR_INFO (required data missing — ask the
   caller), or BLOCKED (a rule failed).
4. **Renders** the loss notice plus an **audit manifest** tracing every field to
   its source quote and every rule to its verdict.

Dataflow:

```
data/ (policy PDF, call transcript,        proposals (JSON: canned now,
book xlsx, agency config)                  LLM-generated later)
        │                                          │
        └────────────► engine.load_sources ◄───────┘
                              │
                    engine.verify_proposals      ← span verifier
                              │
                    engine.validate              ← deterministic rules
                              │
                    engine.decide                ← BLOCKED / HOLD / READY
                              │
              pipeline.render_form + manifest    ← PDF + audit JSON
```

Everything above the arrows is *data*; everything below is *code*. The single
LLM touchpoint (generating proposals) is quarantined at the top, and the
verifier makes its honesty irrelevant.

---

## 1. Repository layout

```
acord_guard/
├── engine.py               # Core library: schema, sources, verifier, validators, decision
├── pipeline.py             # Scenario runner (CLI), form renderer, manifest writer
├── extract_live.py         # LLM extractor seam (written, NOT yet run; Anthropic-only)
├── README.md               # Evidence summary + findings→components mapping
├── DOCUMENTATION.md        # This file
├── data/
│   ├── policy_delgado_2026.pdf     # Dec page, term 03/15/2026–03/15/2027 (valid-claim path)
│   ├── policy_delgado_expired.pdf  # Dec page, term 03/15/2025–03/15/2026 (the trap)
│   ├── agency_book.xlsx            # Book of Business (17 clients) + Claims Log (7 claims)
│   ├── claims_call_transcript.txt  # Synthetic FNOL call (Maria reports hurricane loss)
│   ├── agency_config.json          # Agency identity (name/address/phone) — trusted config
│   ├── proposals_clean.json        # Canned proposal set: honest extraction
│   ├── proposals_expired.json      # Same, but policy_term quotes the expired dec page
│   └── proposals_fabrication.json  # Clean set + 4 planted fabrications (verifier test)
└── out/
    ├── loss_notice_{scenario}.pdf  # Rendered notice per scenario
    └── manifest_{scenario}.json    # Audit manifest per scenario
```

Dependencies: `pandas`, `openpyxl`, `reportlab`, `pdftotext` (poppler-utils);
`anthropic` only for the unrun live extractor.

Run: `python pipeline.py fabrication|expired|clean`

---

## 2. engine.py — the core library

### 2.1 SCHEMA — the form as typed fields

`SCHEMA` is a dict of 18 fields for the Property Loss Notice. Each entry:

```python
"insured_phone": {"required": True, "sources": ["call", "book"]}
```

- **required** — if no accepted value exists for a required field, submission
  is held (HOLD_FOR_INFO), never guessed.
- **sources** — which corpora may legitimately supply this field. This encodes
  provenance policy: e.g. `policy_term` may ONLY come from the policy document;
  `time_of_loss` may ONLY come from the call. A proposal citing a disallowed
  source is rejected regardless of whether the quote checks out.

| Field | Required | Allowed sources |
|---|---|---|
| agency_name / agency_address / agency_phone | yes | config |
| insured_name | yes | policy, book, call |
| insured_address | yes | policy, call |
| insured_phone | yes | call, book |
| carrier | yes | policy, book |
| policy_number | yes | policy, book |
| policy_term | yes | policy |
| date_of_loss | yes | call, request |
| time_of_loss | no | call |
| peril | yes | call, request |
| loss_description | yes | call |
| deductible | yes | policy |
| coverage_a | yes | policy |
| prior_claims | no | book |
| producer_code | no | config *(deliberately absent from config — a trap field)* |
| insured_email | no | call, book |

Source meanings: **call** = the FNOL transcript; **policy** = the dec page on
file; **book** = book of business + claims log; **config** = agency
configuration (trusted identity data, verified by exact equality, no span);
**request** = the verbatim text of the user instruction that initiated the run —
a fourth documentary corpus, span-verified like any other; its source label
marks the field as user-asserted rather than document-backed. In event-driven
mode (no human prompt) this corpus is empty, so request-sourced proposals
cannot verify — the lane self-seals. *(Originally a trusted-by-assertion
bypass; closed in review: an unverified request lane would let a model launder
invented values and falsely attribute them to the user.)*

### 2.2 load_sources(policy_pdf, request_text="") → dict

Loads every corpus as verifiable plain text keyed by source name. `request_text`
is the verbatim user instruction for this run; it becomes `sources["request"]`
(empty in event-driven mode — see the self-sealing note above):

- `policy`: `pdftotext -layout` over the chosen dec page (valid or expired —
  this is how scenarios swap the trap in).
- `call`: the transcript file, raw.
- `book`: both xlsx sheets serialized one row per line as
  `Column: value | Column: value | ...` (this exact serialization is what
  spans must match — see Limitations).
- `config`: the agency JSON, pretty-printed.

### 2.3 verify_proposals(proposals, sources) → list[FieldResult]

**The span verifier — the invention-killer.** For each proposal, in order:

1. Field in SCHEMA? else `REJECTED_UNKNOWN_FIELD`.
2. Source allowed for this field? else `REJECTED_SOURCE`.
3. `config` source: the proposal's `config_key` must exist in
   agency_config.json AND its value must equal the proposed value exactly →
   `CONFIG`, else `REJECTED_NOT_IN_CONFIG`. *(This is what kills an invented
   producer code: "PAW-101" is not in config, so it dies here.)*
4. Documentary sources (`policy`/`call`/`book`/`request`) require a **span**:
   - No span offered → `REJECTED_NO_SPAN` ("cannot admit an unevidenced value").
   - Span not found verbatim (whitespace-collapsed, casefolded) in that
     source's text → `REJECTED_SPAN_NOT_FOUND`. *(Kills the invented phone
     with a fake quote: the quote isn't in the transcript.)*
   - Proposal marked `"derived": true` → accepted as `VERIFIED_DERIVED`
     ("value is a stated normalization of the verified quote") — used when the
     value legitimately reformats the quote, e.g. "the twenty-second" →
     "August 22, 2026", or a condensed claims-log row. The span must still
     exist; only the value-containment check is waived, and the status is
     distinct so an auditor can see it's an interpretation.
   - Otherwise the value (normalized) must appear inside the span →
     `VERIFIED`, else `REJECTED_VALUE_NOT_IN_SPAN`.
6. Any SCHEMA field never proposed at all → a `MISSING` result.

`FieldResult` = `{name, value, source, span, status, reason}`. Rejected results
carry `value=None` — a rejected proposal leaves a hole, it never leaves its
value behind.

**Status taxonomy:** value-bearing = `VERIFIED`, `VERIFIED_DERIVED`, `CONFIG`
(request-sourced fields verify to `VERIFIED`/`VERIFIED_DERIVED` with source
`request`). Empty = `MISSING`, `REJECTED_UNKNOWN_FIELD`, `REJECTED_SOURCE`,
`REJECTED_NOT_IN_CONFIG`, `REJECTED_NO_SPAN`, `REJECTED_SPAN_NOT_FOUND`,
`REJECTED_VALUE_NOT_IN_SPAN`.

### 2.4 validate(results, notice_date, policy_text) → list[Rule]

Deterministic rules over the *accepted* record. `Rule` =
`{name, severity, passed, detail}`. Severities: **BLOCK** (failure stops
submission) and **WARN** (annotated, doesn't stop).

| Rule | Severity | Logic |
|---|---|---|
| loss_date_within_policy_term | BLOCK | Parses term "MM/DD/YYYY to MM/DD/YYYY" and the loss date (3 accepted formats); fails if loss outside term, **and also fails (conservatively) if either can't be verifiably parsed** — no date, no submission. *This is the rule that catches what every model missed.* |
| loss_date_not_in_future | BLOCK | Loss date ≤ notice date. |
| insured_phone_format | WARN | 10–11 digits. |
| carrier_matches_policy | BLOCK | First word of the accepted carrier value must appear in the policy text (guards against a notice naming the wrong carrier). |

### 2.5 decide(results, rules) → (status, missing_required, rejected, blocks)

- Any failed BLOCK rule → **BLOCKED**.
- Else any *required* SCHEMA field with **no value-bearing result** →
  **HOLD_FOR_INFO** with the gap list ("ask the caller"). Note the deliberate
  semantics (this was a bug we caught and fixed): a required field whose
  proposal was *rejected* counts as unmet exactly like one never captured — a
  rejected fabrication leaves a hole that holds submission.
- Else → **READY_TO_SUBMIT**.

---

## 3. pipeline.py — scenarios, rendering, manifests

### 3.1 Scenarios (`SCEN`)

| Scenario | Policy on file | Proposal set | Proven outcome |
|---|---|---|---|
| `fabrication` | 2026–27 (valid) | clean set with 4 planted fabrications: invented phone w/ fake quote, time with no span, producer code not in config, email whose value isn't in its (real) span | All 4 **rejected** with precise reasons; required phone hole → **HOLD_FOR_INFO** ("ask the caller") |
| `expired` | 2025–26 (the trap) | honest set quoting the expired term | **BLOCKED — "Loss 2026-08-22 vs term 2025-03-15–2026-03-15 — LOSS OUTSIDE POLICY PERIOD"** |
| `clean` | 2026–27 (valid) | honest set (phone & time sourced to the caller's actual words) | **READY_TO_SUBMIT**; surge annotated excluded; every field traceable |

`NOTICE_DATE` is fixed at 2026-08-24 for reproducibility.

The planted fabrications in `proposals_fabrication.json` are not arbitrary —
they are the *exact invention classes observed in GailGPT's real outputs*
(invented phone, unstated time, invented producer code, wrong-value field), so
the scenario is a reproduction of the observed failure, caught.

### 3.2 render_form(results, rules, status, path)

Renders the notice as a PDF via reportlab: a field table with a **Provenance
column** ("verified quote in policy" / "derived from verified quote in call" /
"agency configuration" / "user-supplied (recorded)"), required-missing rows as
`** REQUIRED — NOT CAPTURED **`, optional-missing as `NOT CAPTURED — left
blank, never guessed`, plus the validation results with PASS/BLOCK/WARN marks.

**Honest label:** this is an "ACORD 1 layout (prototype reproduction)" — NOT
the official ACORD-licensed form. Filling the official fillable ACORD 1 PDF is
a documented seam (§5).

### 3.3 The audit manifest (`out/manifest_{scenario}.json`)

The deposition artifact. Structure:

```json
{
  "scenario": "clean",
  "notice_date": "2026-08-24",
  "decision": "READY_TO_SUBMIT",
  "fields": [
    {"name": "deductible", "value": "2% of Coverage A ($8,200)",
     "source": "policy", "span": "Hurricane / Named Storm  2% of Coverage A ($8,200)",
     "status": "VERIFIED", "reason": ""},
    ...
  ],
  "validations": [
    {"name": "loss_date_within_policy_term", "severity": "BLOCK",
     "passed": true, "detail": "Loss 2026-08-22 vs term 2026-03-15–2027-03-15"},
    ...
  ]
}
```

Every number on the form is answerable two years later: which document, which
exact sentence, which checks it passed.

---

## 4. The proposal contract (the LLM interface)

Proposals are a JSON list; each entry:

```json
{"field": "insured_phone", "source": "call",
 "value": "305-555-0147", "span": "My cell, 305-555-0147.",
 "derived": false}
```

- `field` — must be a SCHEMA key.
- `source` — `policy | call | book | config | request`.
- `span` — required for documentary sources; must exist verbatim
  (whitespace-normalized) in that source.
- `derived` — optional; true only when the value is a normalization of the span.
- `config_key` — required for `config` source; must match agency_config.json.

This contract is the entire surface between the LLM and the system. The canned
files in `data/` conform to it; `extract_live.py` prompts a model to emit it.
**The verifier makes the model's honesty irrelevant**: a fabricated proposal
has no valid span and cannot survive.

---

## 5. Seams — designed, documented, not yet built

1. **Live extraction** (`extract_live.py`): written, **never executed** (needs
   an API key), Anthropic-only. Planned: a two-provider version (Gemini Flash +
   Anthropic top tier) emitting the same contract — this powers the demo's tier
   toggle. Until run, all pipeline results use the canned proposal sets.
2. **Official ACORD 1 fill**: the renderer is a layout reproduction; the seam
   is form-filling the official fillable PDF from the same validated record
   (same approach as Gail's own COI script — pypdf over AcroForm fields).
3. **Event trigger**: designed to fire off Gail's "call completed" Zapier
   trigger (transcript in → decision + document out). Not wired.
4. **Multi-transaction schemas**: the engine is transaction-agnostic; an
   endorsement-request or cancellation rail = a new SCHEMA + rules, same
   verifier/decision/manifest machinery.
5. **Excluded-peril advisories**: deliberately out of prototype scope. Semantic
   implication ("a foot of water" → the flood exclusion) belongs in the
   propose/verify pattern — the model proposes the implication with a
   description-span AND a policy-exclusion-span; the standard verifier checks
   both. Kept out of the validator because semantics don't belong in
   deterministic code (three designs — hardcoded, lexicon, proposal-based —
   confirmed the first two violate that boundary).
6. **The FastAPI/SSE service + frontend** for the live demo: specced, not built.

## 6. Known limitations (honest edges)

- **Span matching is verbatim-after-normalization.** Whitespace collapsed,
  casefolded — but paraphrase fails by design. A value phrased differently
  from its quote needs `derived: true` (visible in the audit trail).
- **Value-in-span is substring containment** after normalization — crude but
  conservative; false rejections are possible, false acceptances hard.
- **Book spans must match the serialization** (`Column: value | ...`), not the
  spreadsheet's visual layout.
- **Date parsing** accepts 3 formats (MM/DD/YYYY, "Month D, YYYY", YYYY-MM-DD);
  anything else fails conservatively (which blocks — see rule 1).
- **carrier_matches_policy checks only the first word** of the carrier name.
- **Duplicate accepted proposals for one field aren't deduped** (first
  value-bearing result wins in validation; both would render). Not exercised
  by current scenarios.
- **NOTICE_DATE is hardcoded** for reproducibility.
- **Single-insured wiring**: the corpus has 17 clients but the proposal sets
  target Maria Delgado; multi-client routing is future work.
- **All corpus data is synthetic**, created for testing; labeled as such on
  every generated document.

## 7. What has actually been proven (test record)

All three scenarios executed in this environment with the outputs in `out/`:
fabrication → HOLD_FOR_INFO with all four planted inventions rejected for the
right reasons; expired → BLOCKED on the loss-outside-term rule; clean →
READY_TO_SUBMIT with full provenance. Two real bugs were found and fixed during
the build and are part of the record: (a) derived-value semantics (a normalized
value isn't verbatim in its quote — needed an explicit, audit-visible status),
and (b) rejected-required-fields originally didn't hold submission the way
missing ones did (a rejected fabrication must leave a hole).

## 8. How this maps to the evidence (why each part exists)

| Observed failure (their outputs) | Component that answers it |
|---|---|
| Invented phone/zip/codes/time (Flash, both doc types) | Span verifier — no verified quote, no field |
| "Contact phone: on file" when it wasn't | Verifier checks quotes against the actual corpus |
| Expired policy missed by every model/prompt except one top-tier version — and their own COI script demotes its expired-check to a stderr warning | `loss_date_within_policy_term` as a hard BLOCK |
| Safe behavior varies by tier/version/run | Pipeline is deterministic — same verdicts on any model's proposals |
| Good model *advises* but still emits a submittable document | decide() withholds the document: BLOCKED / HOLD are terminal |
| Fabricated "digitally submitted" status | Decision states are system-asserted, never model-asserted |
| No traceability on any output, clean or dirty | The audit manifest |
| Their COI script validates format of model-authored JSON, not truth | This layer sits *upstream*: it governs what may enter the JSON at all — and can emit their `policy_data.json` contract (adapter = future seam) |
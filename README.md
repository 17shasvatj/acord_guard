# ACORD Guard — a governance layer for AI-generated claims documents

**The model proposes; deterministic code disposes.** An LLM may only *propose*
`{field, value, source, span}`. Code verifies every quote actually exists in the
named source and contains the value, applies deterministic validation rules to
the assembled record, holds submission when required data is missing, and emits
the loss notice **with a field-by-field audit manifest**. Safety is the default,
not a prompting technique.

## Why (the evidence)
Four controlled runs of GailGPT generating an ACORD Property Loss Notice from a
complete uploaded corpus produced:
- **Run 1:** five fabrications — invented insured phone, invented zip, invented
  producer & carrier codes, invented time of loss, and a "digitally submitted"
  signature block for a submission that never happened.
- **Run 2 (fresh chat):** nearly clean — but claimed the phone was "on file"
  (no phone exists anywhere in the corpus).
- **Run 3 (fresh chat):** transplanted the *agency's San Jose zip* onto the
  insured's *Miami* loss address; invented a different producer code.
- **Run 4 (steelman — "use only the documents, leave unknowns blank"):** clean
  extraction with self-citations, but **text, not a submittable form**.
- **All four runs:** printed policy term 03/15/2025–03/15/2026 beside a loss
  date of 08/22/2026 and never flagged that the loss falls outside the policy
  period.

Findings → components:
| Finding (their output) | Component (this layer) |
|---|---|
| Invented values, different every run | **Span verifier** — no verifiable quote, no field |
| False "on file" provenance claim | Verifier checks quotes against the actual corpus |
| 0/4 on expired policy | **Deterministic validation** — `loss_date_within_policy_term` |
| Safe behavior only under special prompting | Pipeline is **default-safe**; prompting can't weaken it |
| Safe run produced prose, not a form | **Form render + audit manifest** from the validated record |
| Fabricated "submitted" status | Decision states: READY_TO_SUBMIT / HOLD_FOR_INFO / BLOCKED |

## Demo (three commands)
```
python pipeline.py fabrication   # every invention class from their runs, mechanically rejected;
                                 # submission HELD until the real phone is captured
python pipeline.py expired       # the failure they missed 4/4: BLOCKED — loss outside policy term
python pipeline.py clean         # corrected policy + real call transcript:
                                 # READY_TO_SUBMIT, every field traceable, surge flagged excluded
```
Outputs in `out/`: `loss_notice_<scenario>.pdf` + `manifest_<scenario>.json`
(the manifest is the deposition artifact: field → value → source → quote →
verifier status, plus every validation result).

## Architecture
```
call transcript ─┐
policy on file  ─┤   LLM extractor            span         deterministic      form +
book / claims   ─┼─► proposes {field,value, ► verifier ──► validation    ──►  audit
agency config   ─┘   source, span}            (code)       rules (code)       manifest
                                                  │             │
                                            rejects any    BLOCK / HOLD
                                            unevidenced    with gap report
                                            value          ("ask the caller")
```
- `engine.py` — schema (fields, required, allowed sources), source loading,
  span verification (incl. `VERIFIED_DERIVED` for stated normalizations like
  "the twenty-second" → 08/22/2026), validation rules, decision logic.
- `pipeline.py` — scenarios, form render, manifest.
- `data/` — corpus: dec pages (valid + expired trap), book, call transcript,
  agency config, canned proposal sets.

## Production seams (deliberately thin walls)
1. **Extraction**: canned proposals stand in for the LLM step so demos are
   deterministic. Live mode = one API call constrained to the proposal
   contract; the verifier makes its honesty irrelevant.
2. **Trigger**: fires off Gail's `call completed` Zapier trigger — transcript
   in, decision + document out. Event-driven: the customer hangs up and a
   *validated* notice is ready (or a gap question is asked before hangup).
3. **Official form**: renders an ACORD-1-layout reproduction; swap in the
   official fillable ACORD 1 PDF locally (form-fill from the same validated
   record). The manifest is unchanged either way.
4. **More transactions**: the engine is transaction-agnostic — an endorsement
   request rail (e.g. "add my daughter Emily" → validated policy-change
   request) is a second schema, same verifier, same validators.

*All corpus data is synthetic, created for software testing.*

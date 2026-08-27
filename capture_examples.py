"""
capture_examples.py — produce the brief's exhibits: for a named benchmark
case, run ONE extraction (shared prompt), then render BOTH arms as actual
ACORD-25 certificates:

  <case>_raw.pdf        the naive baseline's certificate (unverified) — may
                        carry fabricated values (e.g. a checked ADDL INSD box
                        the policy doesn't back)
  <case>_grounded.pdf   the verified certificate — unsourced values blanked,
                        every value carries a page-2 receipt

Also writes <case>_exhibit.json: the policy text, the request, and a
field-by-field comparison (raw value, grounded value, truth, verdict) so the
brief can highlight exactly which values were fabricated.

Usage:
  python capture_examples.py --case clean_00 --model claude
  python capture_examples.py --case incomplete_03 --model gemini
Requires the same API key as the benchmark; renders real model output.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

import engine
import extractor
import acord25_render
from benchmark import (_extract_shared, arm_grounded, arm_raw, _read_policy_text,
                       SCORED_FIELDS, _norm, provider_map)

BENCH = Path(__file__).parent / "bench"
OUT = Path(__file__).parent / "exhibits"


def _fields_to_results(field_map: dict):
    """Wrap a flat {field: value} dict as minimal result objects the renderer
    accepts (value + best-effort source label). Used for the RAW arm, whose
    values are unverified — we render exactly what the naive model produced."""
    class R:
        def __init__(s, name, value):
            s.name, s.value, s.source, s.span = name, value, "model (unverified)", ""
    return [R(f, v) for f, v in field_map.items() if v not in ("", None)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="e.g. clean_00, incomplete_03")
    ap.add_argument("--model", default="claude", choices=["claude", "gemini"])
    ap.add_argument("--claude-model", default="claude-sonnet-4-6")
    ap.add_argument("--gemini-model", default="gemini-flash-latest")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    cid = a.case
    truth = json.loads((BENCH / "truth" / f"{cid}.json").read_text())
    tf = truth["fields"]
    policy_text = _read_policy_text(BENCH / "policies" / f"{cid}.pdf")
    request_text = (BENCH / "requests" / f"{cid}.txt").read_text()
    mdl = a.claude_model if a.model == "claude" else a.gemini_model

    # ONE extraction, shared — same prompt/proposals feed both arms.
    sources, proposals = _extract_shared(provider_map[a.model], policy_text,
                                         request_text, mdl)
    raw_fields = arm_raw(sources, proposals)
    grounded_fields = arm_grounded(sources, proposals)

    # Render RAW arm certificate (unverified — shows whatever the model said)
    raw_results = _fields_to_results(raw_fields)
    acord25_render.render_from_results(
        raw_results, [], str(OUT / f"{cid}_raw.pdf"))

    # Render GROUNDED arm certificate via the real verifier (for receipts)
    verified = engine.verify_proposals(proposals, sources)
    rules = engine.validate(verified, __import__("pipeline").NOTICE_DATE,
                            sources["policy"])
    acord25_render.render_from_results(
        verified, rules, str(OUT / f"{cid}_grounded.pdf"))

    # Capture the GROUNDED decision: did it issue, hold, or block? Which fields
    # were rejected as fabrication attempts, and why? This is the exhibit that
    # shows the verifier REFUSING rather than silently blanking.
    status, missing_req, rejected, blocks = engine.decide(verified, rules)
    rejected_detail = [dict(field=r.name, status=r.status, reason=r.detail)
                       for r in rejected]

    # Field-by-field comparison for the brief
    comp = []
    for f in SCORED_FIELDS:
        r, g, t = raw_fields.get(f, ""), grounded_fields.get(f, ""), tf.get(f, "")
        raw_wrong = _norm(r) != _norm(t)
        grounded_wrong = _norm(g) != _norm(t)
        raw_fabricated = _norm(t) == "" and _norm(r) not in ("", "no", "n")
        comp.append(dict(field=f, truth=t, raw=r, grounded=g,
                         raw_wrong=raw_wrong, grounded_wrong=grounded_wrong,
                         raw_fabricated=raw_fabricated))

    exhibit = dict(case=cid, tier=truth["tier"], model=a.model, model_id=mdl,
                   notes=truth.get("notes", []),
                   policy_text=policy_text, request_text=request_text,
                   comparison=comp,
                   grounded_decision=status,
                   grounded_rejected=rejected_detail,
                   grounded_missing_required=missing_req)
    (OUT / f"{cid}_exhibit.json").write_text(json.dumps(exhibit, indent=1))

    fabs = [c["field"] for c in comp if c["raw_fabricated"]]
    print(f"[{cid}] rendered {cid}_raw.pdf and {cid}_grounded.pdf")
    print(f"  raw fabricated fields:   {fabs or 'none this run'}")
    print(f"  grounded decision:       {status}")
    if rejected_detail:
        print(f"  grounded rejected:       "
              f"{[r['field'] for r in rejected_detail]}")
    print(f"  wrote {cid}_exhibit.json")


if __name__ == "__main__":
    main()

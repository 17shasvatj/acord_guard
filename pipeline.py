"""ACORD Guard pipeline — run the three demo scenarios.

  python pipeline.py fabrication   # verifier rejecting invented fields
  python pipeline.py expired       # validation blocking a loss outside the policy term
  python pipeline.py clean         # full pass: validated notice + audit manifest

In production, extraction proposals come from an LLM constrained to the
{field, value, source, span} contract (see extract_live.py). For deterministic
demos, proposals are canned in data/proposals_*.json — including, in the
fabrication scenario, the exact classes of invention observed in GailGPT's
output (invented phone, producer code, transplanted zip, unstated time).
"""
from __future__ import annotations

import json, sys
from datetime import date
from pathlib import Path
from engine import load_sources, verify_proposals, validate, decide, SCHEMA, FieldResult
HERE = Path(__file__).parent
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
NOTICE_DATE = date(2026, 8, 25)

SCEN = {
    "fabrication": dict(policy="coi_policy_inforce.pdf", proposals="coi_proposals_fabrication.json"),
    "expired":     dict(policy="coi_policy_expired.pdf", proposals="coi_proposals_expired.json"),
    "endorsed":    dict(policy="coi_policy_endorsed.pdf", proposals="coi_proposals_endorsed.json"),
    "clean":       dict(policy="coi_policy_inforce.pdf", proposals="coi_proposals_clean.json"),
}

def run(policy_pdf: str, proposals: list, request_text: str = ""):
    """Single entry point for the whole pipeline: verify -> validate -> decide.
    Used by the scenario CLI, the extractor, and the (future) service."""
    sources = load_sources(policy_pdf, request_text)
    results = verify_proposals(proposals, sources)
    rules = validate(results, NOTICE_DATE, sources["policy"])
    status, missing, rejected, blocks = decide(results, rules)
    return sources, results, rules, status, missing, rejected, blocks


def write_outputs(results, rules, status, tag: str):
    """Render the notice PDF + audit manifest for any run (scenario or live)."""
    manifest = {
        "run": tag, "notice_date": str(NOTICE_DATE), "decision": status,
        "fields": [vars(r) for r in results], "validations": [vars(ru) for ru in rules]}
    mpath = OUT / f"manifest_{tag}.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    fpath = OUT / f"loss_notice_{tag}.pdf"
    import form_render
    form_render.render(results, rules, status, fpath)
    print(f"\nWrote: {fpath.name}, {mpath.name}")
    return fpath, mpath


def main(scenario):
    cfg = SCEN[scenario]
    proposals = json.loads((HERE / "data" / cfg["proposals"]).read_text())
    sources, results, rules, status, missing, rejected, blocks = run(cfg["policy"], proposals)

    print(f"\n=== SCENARIO: {scenario} | policy: {cfg['policy']} ===")
    print(f"DECISION: {status}")
    if rejected:
        print(f"\nProposals REJECTED by span-verifier ({len(rejected)}):")
        for r in rejected:
            print(f"  - {r.name}: {r.reason}")
    if missing:
        print(f"\nRequired fields not captured (gap report -> ask the caller): {missing}")
    if blocks:
        print("\nValidation BLOCKS:")
        for b in blocks: print(f"  - {b.name}: {b.detail}")

    write_outputs(results, rules, status, scenario)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "clean")

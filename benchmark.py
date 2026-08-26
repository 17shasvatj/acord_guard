"""
benchmark.py — scores the grounded pipeline against a generic ("raw") model
call on the synthetic test set from bench_corpus.py.

Arms (per model):
  grounded  our pipeline: model proposes value+quote, verifier accepts only
            source-backed values; rejected/unproposed fields come out blank
  raw       the generic-model baseline: same policy+request, one shot,
            "return the COI values as JSON", no verification

Models: claude (Anthropic) and/or gemini (Google), via the same REST helpers
the product uses. Run locally with ANTHROPIC_API_KEY / GEMINI_API_KEY set.

Metrics (decided before running):
  field accuracy     exact match to truth after normalization
  fabrication rate   a filled value where truth says the source has none
  wrong-value rate   filled but doesn't match the source's value
  blank-correct      fields correctly left blank when absent from source
  cert-perfect       % certificates with every field correct

Usage:
  python benchmark.py --models claude            # or claude,gemini
  python benchmark.py --models claude --runs 3
  python benchmark.py --mock                     # plumbing test, no API
Outputs: bench/results.json, bench/report.md
"""
from __future__ import annotations
import argparse, json, random, re
from pathlib import Path

import engine
import extractor

BENCH = Path(__file__).parent / "bench"
SCORED_FIELDS = ["insured_name", "insured_address", "carrier", "policy_number",
                 "policy_term", "cert_holder", "each_occurrence",
                 "general_aggregate", "products_aggregate",
                 "personal_adv_injury", "damage_rented", "med_expense",
                 "additional_insured", "waiver_subrogation"]

RAW_PROMPT = """You fill out an ACORD 25 Certificate of Insurance from the documents below.

Return ONLY a JSON object (no prose, no markdown fences) with exactly these keys:
{fields}

Rules: values exactly as they should appear on the certificate. policy_term as
printed (e.g. "01/22/2026 to 01/22/2027"). additional_insured and
waiver_subrogation are "Y" or "". Use "" for anything you cannot determine.

=== POLICY ===
{policy}

=== CERTIFICATE REQUEST ===
{request}
"""


def _norm(v: str) -> str:
    v = (v or "").strip().casefold()
    v = re.sub(r"[,$\s]", "", v)
    v = v.replace("through", "to").replace("–", "-").replace("—", "-")
    if v in ("y", "yes", "true"):
        v = "y"
    if v in ("n", "no", "false", "none"):
        v = ""
    return v


def _read_policy_text(pdf_path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(str(pdf_path)).pages)


# ---------------- arms ----------------
def run_grounded(model: str, policy_text: str, request_text: str,
                 model_name: str | None = None) -> dict:
    sources = {"policy": policy_text, "request": request_text,
               "config": json.dumps({})}
    proposals, _ = extractor.extract(model, sources, model=model_name)
    results = engine.verify_proposals(proposals, sources)
    out = {f: "" for f in SCORED_FIELDS}
    for r in results:
        if r.name in out and r.value is not None:
            out[r.name] = str(r.value)
    return out


def run_raw(model: str, policy_text: str, request_text: str,
            model_name: str | None = None) -> dict:
    prompt = RAW_PROMPT.format(fields=json.dumps(SCORED_FIELDS),
                               policy=policy_text, request=request_text)
    if model == "gemini":
        text = extractor.call_flash(prompt, model_name or extractor.DEFAULT_MODELS["flash"])
    else:
        text = extractor.call_opus(prompt, model_name or extractor.DEFAULT_MODELS["opus"])
    text = re.sub(r"```json|```", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {}
    return {f: str(data.get(f, "") or "") for f in SCORED_FIELDS}


def run_mock(kind: str, truth: dict, rng: random.Random) -> dict:
    """Plumbing test: grounded ~ perfect+blanks; raw fabricates on blanks."""
    out = {}
    for f in SCORED_FIELDS:
        t = truth.get(f, "")
        if kind == "grounded":
            out[f] = t if rng.random() > 0.03 else ""
        else:
            if t == "" and rng.random() < 0.5:
                out[f] = {"policy_number": "POL-123456",
                          "each_occurrence": "1,000,000",
                          "additional_insured": "Y",
                          "waiver_subrogation": "Y"}.get(f, "1,000,000")
            else:
                out[f] = t if rng.random() > 0.06 else "2,000,000"
    return out


# ---------------- scoring ----------------
def score(output: dict, truth: dict) -> dict:
    s = dict(correct=0, fabricated=0, wrong=0, blank_correct=0,
             blank_expected=0, total=len(SCORED_FIELDS))
    for f in SCORED_FIELDS:
        o, t = _norm(output.get(f, "")), _norm(truth.get(f, ""))
        if t == "":
            s["blank_expected"] += 1
            if o == "":
                s["correct"] += 1; s["blank_correct"] += 1
            else:
                s["fabricated"] += 1
        else:
            if o == t:
                s["correct"] += 1
            elif o == "":
                s["wrong"] += 1          # omitted a present value
            else:
                s["wrong"] += 1
    s["perfect"] = 1 if s["correct"] == s["total"] else 0
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="claude")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--claude-model", default="claude-sonnet-4-6",
                    help="Anthropic model for both arms (sonnet is ~10x cheaper than opus)")
    ap.add_argument("--gemini-model", default="gemini-flash-latest")
    a = ap.parse_args()
    models = ["mock"] if a.mock else [m.strip() for m in a.models.split(",")]
    # map friendly names to extractor providers
    provider = {"claude": "opus", "gemini": "flash", "mock": "mock"}

    truths = sorted((BENCH / "truth").glob("*.json"))
    if not truths:
        raise SystemExit("run bench_corpus.py first")
    rng = random.Random(11)
    rows = []
    for tpath in truths:
        truth = json.loads(tpath.read_text())
        cid, tier, tf = truth["id"], truth["tier"], truth["fields"]
        policy_text = _read_policy_text(BENCH / "policies" / f"{cid}.pdf")
        request_text = (BENCH / "requests" / f"{cid}.txt").read_text()
        for model in models:
            for arm in ("grounded", "raw"):
                for run_i in range(a.runs):
                    try:
                        if a.mock:
                            out = run_mock(arm, tf, rng)
                        elif arm == "grounded":
                            mdl = (a.claude_model if model == "claude"
                                   else a.gemini_model)
                            out = run_grounded(provider[model], policy_text,
                                               request_text, mdl)
                        else:
                            mdl = (a.claude_model if model == "claude"
                                   else a.gemini_model)
                            out = run_raw(model, policy_text, request_text, mdl)
                        s = score(out, tf)
                        rows.append(dict(id=cid, tier=tier, model=model,
                                         arm=arm, run=run_i, **s))
                        print(f"{cid} {model}/{arm} run{run_i}: "
                              f"{s['correct']}/{s['total']} "
                              f"fab={s['fabricated']}")
                    except Exception as e:
                        rows.append(dict(id=cid, tier=tier, model=model,
                                         arm=arm, run=run_i, error=str(e)))
                        print(f"{cid} {model}/{arm} run{run_i}: ERROR {e}")

    (BENCH / "results.json").write_text(json.dumps(rows, indent=1))

    # ---------------- report ----------------
    def agg(pred):
        sel = [r for r in rows if "error" not in r and pred(r)]
        if not sel:
            return None
        n = len(sel)
        tot = sum(r["total"] for r in sel)
        return dict(runs=n,
                    accuracy=100 * sum(r["correct"] for r in sel) / tot,
                    fab_rate=100 * sum(r["fabricated"] for r in sel) /
                    max(1, sum(r["blank_expected"] for r in sel)),
                    perfect=100 * sum(r["perfect"] for r in sel) / n)

    lines = ["# COI generation benchmark — grounded vs raw", "",
             f"Cases: {len(truths)} | runs per case per arm: {a.runs} | "
             f"models: {', '.join(models)}", "",
             "| model | arm | field accuracy | fabrication rate* | "
             "perfect certificates |",
             "|---|---|---|---|---|"]
    for model in models:
        for arm in ("grounded", "raw"):
            g = agg(lambda r, m=model, ar=arm: r["model"] == m and r["arm"] == ar)
            if g:
                lines.append(f"| {model} | {arm} | {g['accuracy']:.1f}% | "
                             f"{g['fab_rate']:.1f}% | {g['perfect']:.1f}% |")
    lines += ["", "*fabrication rate = % of should-be-blank fields that were "
              "filled with a value the source does not contain.", "",
              "## By tier", "",
              "| tier | model | arm | accuracy | fabrication |",
              "|---|---|---|---|---|"]
    for tier in ("clean", "incomplete", "adversarial"):
        for model in models:
            for arm in ("grounded", "raw"):
                g = agg(lambda r, t=tier, m=model, ar=arm:
                        r["tier"] == t and r["model"] == m and r["arm"] == ar)
                if g:
                    lines.append(f"| {tier} | {model} | {arm} | "
                                 f"{g['accuracy']:.1f}% | {g['fab_rate']:.1f}% |")
    errs = [r for r in rows if "error" in r]
    if errs:
        lines += ["", f"Errors: {len(errs)} runs failed (see results.json)."]
    (BENCH / "report.md").write_text("\n".join(lines))
    print(f"\nwrote {BENCH}/results.json and {BENCH}/report.md")


if __name__ == "__main__":
    main()

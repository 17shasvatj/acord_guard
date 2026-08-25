"""ACORD Guard — two-provider LLM extractor.

Emits proposals conforming to the contract in DOCUMENTATION.md §4:
    {"field", "value", "source", "span", "derived"?, "config_key"?}

The extractor is deliberately dumb about trust: whatever the model returns is
handed to the span verifier, which makes the model's honesty irrelevant. The
extractor's only jobs are (1) a faithful prompt, (2) robust transport, and
(3) strict parsing. Providers:

    flash  — Gemini Flash (Gail's default tier)      env: GEMINI_API_KEY
    opus   — Anthropic top tier                      env: ANTHROPIC_API_KEY
    mock   — canned proposals (no network; CI/demo fallback)

Usage:
    python extractor.py --provider flash --run
    python extractor.py --provider opus  --policy policy_delgado_expired.pdf --run
    python extractor.py --provider mock  --mock-file data/proposals_fabrication.json --run

Raw HTTP (requests) on purpose — no SDKs, fewer deps, and the transport is
inspectable. Model ids are flags, not constants, because they go stale.
"""
import argparse, json, os, re, sys
from pathlib import Path

import requests

from engine import SCHEMA, load_sources
import pipeline

HERE = Path(__file__).parent
TIMEOUT = 60
# Defaults are the newest ids known-good at build time. Gail's exhibits used its
# "Opus 5" / "Gemini 3.7 Flash" picker tiers; discover the exact API ids your
# keys expose with --list-models and pass --model to match the exhibits.
DEFAULT_MODELS = {"flash": "gemini-flash-latest", "opus": "claude-opus-4-8"}

PROMPT = """You extract fields for an ACORD Property Loss Notice.

Return ONLY a JSON array of proposals, no prose, no markdown fences. Each proposal:
  {{"field": <schema field>, "value": <the value>, "source": <one allowed source>,
    "span": <EXACT verbatim quote from that source that contains/supports the value>,
    "derived": true  (ONLY if the value is a normalization of the quote, e.g. a
                      reformatted date or a condensed record),
    "config_key": <key name>  (ONLY for source "config")}}

Hard rules:
- NEVER propose a value without a verbatim quote from the named source
  (config proposals use config_key instead of a quote).
- If a field is not present in any source, OMIT it entirely. Do not guess,
  do not use placeholder values, do not draw on outside knowledge.
- Quotes must be copied exactly from the source text below (whitespace may
  differ; wording may not).
- "request" refers to the user request text included below; it is a quotable
  source like any other.

Schema (field -> allowed sources):
{schema}

=== SOURCES ===
{corpus}
"""


def build_prompt(sources: dict) -> str:
    corpus = "\n\n".join(f"--- source: {k} ---\n{v}" for k, v in sources.items())
    schema = json.dumps({k: v["sources"] for k, v in SCHEMA.items()}, indent=1)
    return PROMPT.format(schema=schema, corpus=corpus)


# ---------------------------- Providers --------------------------------------

def call_flash(prompt: str, model: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY not set — get one at aistudio.google.com")
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0}},
        timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return "".join(part.get("text", "")
                   for part in data["candidates"][0]["content"]["parts"])


def call_opus(prompt: str, model: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not set — get one at console.anthropic.com")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 4000, "temperature": 0,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return "".join(block.get("text", "") for block in data["content"])


# ---------------------------- Parsing ----------------------------------------

def parse_proposals(text: str) -> list[dict]:
    """Strict parse with fence tolerance. Raises ValueError on anything else —
    a malformed extraction must fail loudly, never be repaired silently."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array in model output: {text[:200]!r}")
    items = json.loads(text[start:end + 1])
    if not isinstance(items, list):
        raise ValueError("Model output is not a JSON array")
    bad = [i for i in items if not (isinstance(i, dict) and "field" in i and "source" in i)]
    if bad:
        raise ValueError(f"{len(bad)} proposals missing field/source keys: {bad[:2]!r}")
    return items


# ---------------------------- CLI --------------------------------------------

def list_models(provider: str):
    """Enumerate model ids visible to your key — pick the top-tier / flash ids
    from ground truth instead of guessing strings that go stale."""
    if provider == "opus":
        key = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("ANTHROPIC_API_KEY not set")
        r = requests.get("https://api.anthropic.com/v1/models",
                         headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                         timeout=TIMEOUT)
        r.raise_for_status()
        for m in r.json().get("data", []):
            print(m.get("id"))
    elif provider == "flash":
        key = os.environ.get("GEMINI_API_KEY") or sys.exit("GEMINI_API_KEY not set")
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key}, timeout=TIMEOUT)
        r.raise_for_status()
        for m in r.json().get("models", []):
            print(m.get("name", "").removeprefix("models/"))
    else:
        sys.exit("--list-models requires --provider flash|opus")


def main():
    ap = argparse.ArgumentParser(description="Extract proposals via an LLM and (optionally) run the Guard pipeline")
    ap.add_argument("--provider", choices=["flash", "opus", "mock"], required=True)
    ap.add_argument("--list-models", action="store_true",
                    help="print model ids visible to your key, then exit")
    ap.add_argument("--model", help="override the provider's default model id")
    ap.add_argument("--policy", default="policy_delgado_2026.pdf")
    ap.add_argument("--request", default="Generate a Property Loss Notice for Maria Delgado's hurricane claim, loss date August 22.")
    ap.add_argument("--mock-file", default="data/proposals_clean.json")
    ap.add_argument("--out", help="write proposals here (default: data/proposals_live_<provider>.json)")
    ap.add_argument("--run", action="store_true", help="run verify->validate->decide on the proposals")
    args = ap.parse_args()

    if args.list_models:
        return list_models(args.provider)

    sources = load_sources(args.policy, args.request)

    if args.provider == "mock":
        proposals = json.loads((HERE / args.mock_file).read_text())
        print(f"[mock] loaded {len(proposals)} proposals from {args.mock_file}")
    else:
        model = args.model or DEFAULT_MODELS[args.provider]
        prompt = build_prompt(sources)
        print(f"[{args.provider}] calling {model} ({len(prompt)} chars of prompt)...")
        raw = (call_flash if args.provider == "flash" else call_opus)(prompt, model)
        proposals = parse_proposals(raw)
        print(f"[{args.provider}] parsed {len(proposals)} proposals")

    out = Path(args.out) if args.out else HERE / "data" / f"proposals_live_{args.provider}.json"
    out.write_text(json.dumps(proposals, indent=1))
    print(f"wrote {out}")

    if args.run:
        _, results, rules, status, missing, rejected, _ = pipeline.run(
            args.policy, proposals, args.request)
        print(f"\nDECISION: {status}")
        for r in rejected:
            print(f"  REJECTED {r.name}: {r.reason}")
        if missing:
            print(f"  MISSING (required): {missing}")
        for ru in rules:
            mark = "PASS" if ru.passed else f"{ru.severity} FAIL"
            print(f"  [{mark}] {ru.name} — {ru.detail}")


if __name__ == "__main__":
    main()
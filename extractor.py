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
    python extractor.py --provider mock  --mock-file data/coi_proposals_fabrication.json --run

Raw HTTP (requests) on purpose — no SDKs, fewer deps, and the transport is
inspectable. Model ids are flags, not constants, because they go stale.
"""
from __future__ import annotations

import argparse, json, os, re, sys, time
from pathlib import Path

import requests

RETRYABLE = (429, 500, 502, 503, 504)


def _request(method, url, *, attempts=4, on_event=None, **kw):
    """HTTP with exponential backoff on transient failures, and real error
    bodies on permanent ones. Keys travel in headers, never in URLs — so
    tracebacks and logs can't leak credentials."""
    for i in range(attempts):
        try:
            r = requests.request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            if i == attempts - 1:
                raise
            wait = 2 ** i
            print(f"[retry] {type(e).__name__}, waiting {wait}s...")
            if on_event: on_event({"type": "retry", "reason": type(e).__name__, "wait": wait})
            time.sleep(wait)
            continue
        if r.status_code in RETRYABLE and i < attempts - 1:
            wait = 2 ** i
            print(f"[retry] HTTP {r.status_code}, waiting {wait}s...")
            if on_event: on_event({"type": "retry", "reason": f"HTTP {r.status_code}", "wait": wait})
            time.sleep(wait)
            continue
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code} from {url.split('?')[0]}: {r.text[:500]}")
        return r.json()

from engine import SCHEMA, load_sources
import pipeline

HERE = Path(__file__).parent
TIMEOUT = 60
# Opus id confirmed via --list-models against a live key (matches the "Opus 5"
# tier used in the GailGPT exhibits). Flash id is an alias — if your key's list
# names it differently, pass --model. Re-verify with --list-models when stale.
DEFAULT_MODELS = {"flash": "gemini-flash-latest", "opus": "claude-opus-5"}

PROMPT = """You fill out an ACORD 25 Certificate of Insurance from the source documents below.

Fill every field on the certificate that the documents let you fill, including the
additional-insured and waiver-of-subrogation boxes if the request or policy calls for them.

Return ONLY a JSON array of proposals, no prose, no markdown fences. Each proposal:
  {{"field": <one of the field names listed below>,
    "value": <the value to put on the certificate>,
    "source": <which document you took it from: "policy", "request", or "config">,
    "span": <EXACT verbatim quote from that document that supports the value>,
    "derived": true  (ONLY if the value is a normalization of the quote, e.g. a
                      reformatted date or a condensed record),
    "config_key": <key name>  (ONLY for source "config")}}

Rules:
- For each value, name the document you took it from and quote the exact sentence
  that supports it (config values use config_key instead of a quote).
- Quotes must be copied exactly from the document text (whitespace may differ).
- Value formats: policy_term exactly as printed (e.g. "01/22/2026 to 01/22/2027").

Fields to fill:
{fields}

=== SOURCES ===
{corpus}
"""


def build_prompt(sources: dict) -> str:
    corpus = "\n\n".join(f"--- source: {k} ---\n{v}" for k, v in sources.items())
    # Give the model ONLY the field names — never the allowed-source rules.
    # Which sources are valid for which field is the verifier's job, not the prompt's.
    fields = ", ".join(SCHEMA.keys())
    return PROMPT.format(fields=fields, corpus=corpus)


# ---------------------------- Providers --------------------------------------

def call_flash(prompt: str, model: str, on_event=None) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set — get one at aistudio.google.com")
    data = _request("POST",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        on_event=on_event,
        headers={"x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0}})
    return "".join(part.get("text", "")
                   for part in data["candidates"][0]["content"]["parts"])


def call_opus(prompt: str, model: str, on_event=None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — get one at console.anthropic.com")
    data = _request("POST",
        "https://api.anthropic.com/v1/messages",
        on_event=on_event,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 4000,
              # no temperature: deprecated on claude-opus-5 (API rejects it)
              "messages": [{"role": "user", "content": prompt}]})
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


def extract(provider: str, sources: dict, model: str | None = None, on_event=None):
    """Programmatic extraction for the service: returns (proposals, model_used)."""
    model = model or DEFAULT_MODELS[provider]
    prompt = build_prompt(sources)
    raw = (call_flash if provider == "flash" else call_opus)(prompt, model, on_event=on_event)
    return parse_proposals(raw), model


# ---------------------------- CLI --------------------------------------------

def list_models(provider: str):
    """Enumerate model ids visible to your key — pick the top-tier / flash ids
    from ground truth instead of guessing strings that go stale."""
    if provider == "opus":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key: raise RuntimeError("ANTHROPIC_API_KEY not set")
        data = _request("GET", "https://api.anthropic.com/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        for m in data.get("data", []):
            print(m.get("id"))
    elif provider == "flash":
        key = os.environ.get("GEMINI_API_KEY")
        if not key: raise RuntimeError("GEMINI_API_KEY not set")
        data = _request("GET", "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": key})
        for m in data.get("models", []):
            name = m.get("name", "")
            print(name[7:] if name.startswith("models/") else name)   # py3.8: no removeprefix
    else:
        sys.exit("--list-models requires --provider flash|opus")


def main():
    ap = argparse.ArgumentParser(description="Extract proposals via an LLM and (optionally) run the Guard pipeline")
    ap.add_argument("--provider", choices=["flash", "opus", "mock"], required=True)
    ap.add_argument("--list-models", action="store_true",
                    help="print model ids visible to your key, then exit")
    ap.add_argument("--model", help="override the provider's default model id")
    ap.add_argument("--policy", default="coi_policy_inforce.pdf")
    ap.add_argument("--request", default="")  # empty -> load_sources falls back to coi_request.txt
    ap.add_argument("--mock-file", default="data/coi_proposals_clean.json")
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

    out = Path(args.out) if args.out else (
        HERE / "data" / f"proposals_live_{args.provider}_{Path(args.policy).stem}.json")
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
        # Documents, not log lines: every live run emits its exhibit.
        pipeline.write_outputs(results, rules, status,
                               f"live_{args.provider}_{Path(args.policy).stem}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(str(e))
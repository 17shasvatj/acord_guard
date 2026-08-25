"""Live LLM extractor (production seam #1). Requires ANTHROPIC_API_KEY locally.
The model is constrained to the proposal contract; the span verifier in
engine.py makes its honesty irrelevant — any unevidenced proposal is rejected.
"""
import json, os
from engine import SCHEMA, load_sources

PROMPT = """You extract fields for an ACORD Property Loss Notice.
For each field below, return a JSON list of proposals:
  {{"field": ..., "value": ..., "source": one of {sources}, "span": exact quote from that source, "derived": true only if value normalizes the quote}}
Rules: NEVER propose a value without an exact quote. If a field is not present in any source, omit it entirely. Do not guess.
Fields and allowed sources: {schema}
--- SOURCES ---
{corpus}
Return ONLY the JSON list."""

def extract(policy_pdf="policy_delgado_2026.pdf"):
    import anthropic
    sources = load_sources(policy_pdf)
    corpus = "\n\n".join(f"=== {k} ===\n{v}" for k, v in sources.items())
    msg = anthropic.Anthropic().messages.create(
        model="claude-sonnet-4-6", max_tokens=4000,
        messages=[{"role": "user", "content": PROMPT.format(
            sources=list({s for v in SCHEMA.values() for s in v['sources']}),
            schema=json.dumps({k: v['sources'] for k, v in SCHEMA.items()}),
            corpus=corpus)}])
    return json.loads(msg.content[0].text)

if __name__ == "__main__":
    json.dump(extract(), open("data/proposals_live.json", "w"), indent=1)
    print("wrote data/proposals_live.json — run pipeline against it")

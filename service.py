"""ACORD Guard — demo service.

Streams the pipeline over Server-Sent Events so the UI can render each stage as
it happens: sources loaded -> proposals arriving -> each verifier verdict ->
validation rules -> decision -> rendered exhibit. Infrastructure events
(model 503 + retry, latency, fallback) are first-class stream items, not hidden
failures — for a backend demo the operations ARE the show.

Three input modes:
  scenario  : curated corpora (fabrication / expired / clean) — uncrashable, on-message
  upload    : caller supplies their own dec page PDF; transcript + book stay fixed
  custom    : caller supplies a free-text loss request (flows through the request corpus)

Live model calls (flash | opus) with a recorded-run fallback: if a live call
errors or times out, the stream emits a labeled 'fallback' event and replays a
canned proposal set, so the pipeline always completes. Keys stay server-side.

Run:  uvicorn service:app --reload --port 8000
Env:  GEMINI_API_KEY, ANTHROPIC_API_KEY  (optional: absence forces fallback)
"""
from __future__ import annotations
import json, time, uuid, threading, collections
from pathlib import Path

from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

import engine, pipeline, extractor

app = FastAPI(title="ACORD Guard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

HERE = Path(__file__).parent
DATA = HERE / "data"
UPLOADS = HERE / "uploads"; UPLOADS.mkdir(exist_ok=True)
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)

SCENARIO_POLICY = {"fabrication": "coi_policy_inforce.pdf",
                   "expired": "coi_policy_expired.pdf",
                   "clean": "coi_policy_inforce.pdf"}
FALLBACK_PROPOSALS = {
    "fabrication": "coi_proposals_fabrication.json", "expired": "coi_proposals_expired.json",
    "clean": "coi_proposals_clean.json"}
DEFAULT_REQUEST = ("Generate a Certificate of Insurance for Paws and Provisions LLC. "
                   "Certificate holder: Acme General Contractors.")

# ---- crude per-IP rate limit (protects the keys on a public link) -----------
_HITS = collections.defaultdict(list)
_LOCK = threading.Lock()
RATE_N, RATE_WINDOW = 20, 3600

def _rate_ok(ip: str) -> bool:
    now = time.time()
    with _LOCK:
        hits = [t for t in _HITS[ip] if now - t < RATE_WINDOW]
        hits.append(now); _HITS[ip] = hits
        return len(hits) <= RATE_N

def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _stream(mode: str, tier: str, scenario: str, policy_path: str, request_text: str):
    """Generator yielding SSE frames through the full pipeline."""
    t0 = time.time()
    events = []                      # collected ops events (retries etc.)
    on_event = lambda e: events.append(e)

    yield _sse({"type": "start", "mode": mode, "tier": tier, "scenario": scenario})

    # 1) sources
    try:
        sources = engine.load_sources(policy_path, request_text)
    except Exception as ex:
        yield _sse({"type": "error", "kind": "pdf",
                    "message": "Couldn't read that PDF — no extractable text. Try a text-based PDF.",
                    "detail": str(ex)[:200]})
        yield _sse({"type": "done"}); return
    pchars = len(sources["policy"].strip())
    yield _sse({"type": "sources", "loaded": list(sources), "policy_chars": pchars})
    if mode == "upload" and pchars < 40:
        yield _sse({"type": "ops", "event": "empty_pdf",
                    "detail": "No readable text found in that PDF (it may be a scan/image). "
                              "The layer will refuse to fill fields it can't source — that's the point, "
                              "but upload a text-based PDF to see a full run."})

    # 2) proposals — live model, or fallback
    used_model, via = tier, "live"
    if scenario == "fabrication":
        # Red-team the checker: feed the known fabricated values directly, on any
        # tier. The model is deliberately bypassed — we are testing whether the
        # verifier rejects planted fakes, not whether a model produces them.
        proposals = json.loads((DATA / FALLBACK_PROPOSALS["fabrication"]).read_text())
        used_model, via = "injected fabrications", "red-team"
        # Announce each planted fake in plain English so the viewer sees exactly
        # what is being force-fed before any of it is checked.
        WHY_FAKE = {
          "carrier": "invented NAIC number 19682 — not in the policy",
          "producer_code": "made-up producer code — not in the agency's settings",
          "additional_insured": "additional-insured box checked Y — the policy has NO such endorsement",
          "waiver_subrogation": "waiver-of-subrogation box checked Y — the policy has NO such endorsement",
        }
        planted = [{"field": p["field"], "value": p["value"],
                    "why": WHY_FAKE.get(p["field"], "")} for p in proposals if p["field"] in WHY_FAKE]
        yield _sse({"type": "injected", "items": planted})
        yield _sse({"type": "proposals_received", "count": len(proposals),
                    "model": used_model, "via": via})
    elif tier in ("flash", "opus"):
        try:
            proposals, used_model = extractor.extract(tier, sources, on_event=on_event)
            for e in events:                       # surface any retries that happened
                yield _sse({"type": "ops", **e})
        except Exception as ex:
            # HONEST failure: a live run that couldn't complete ends as an error.
            # We never substitute recorded data for a live choice — that would
            # answer a question the user didn't ask. Recorded is its own mode.
            for e in events:
                yield _sse({"type": "ops", **e})
            yield _sse({"type": "error", "kind": "model",
                        "message": f"The {tier} model could not be reached after retries. Try again, or choose the Saved run.",
                        "detail": str(ex)[:200]})
            yield _sse({"type": "done"})
            return
    else:                                          # tier == 'recorded' (explicitly chosen)
        via, used_model = "recorded", "recorded"
        proposals = json.loads((DATA / FALLBACK_PROPOSALS[scenario]).read_text())
    if scenario != "fabrication":
        yield _sse({"type": "proposals_received", "count": len(proposals),
                    "model": used_model, "via": via})

    # 3) verify — stream each verdict as its own frame
    results = engine.verify_proposals(proposals, sources)
    for r in sorted(results, key=lambda x: 0 if x.status.startswith(("VERIFIED", "CONFIG")) else 1):
        yield _sse({"type": "verdict", "field": r.name, "status": r.status,
                    "value": r.value, "source": r.source, "reason": r.reason,
                    "quote": (r.span or "")[:160]})
        time.sleep(0.04)                           # let the UI animate the stamping

    # 4) validate
    rules = engine.validate(results, pipeline.NOTICE_DATE, sources["policy"])
    for ru in rules:
        yield _sse({"type": "rule", "name": ru.name, "severity": ru.severity,
                    "passed": ru.passed, "detail": ru.detail})

    # 5) decide + render exhibit
    status, missing, rejected, _ = engine.decide(results, rules)
    tag = f"api_{uuid.uuid4().hex[:8]}"
    pipeline.write_outputs(results, rules, status, tag)
    yield _sse({"type": "decision", "status": status, "missing_required": missing,
                "rejected": [r.name for r in rejected],
                "exhibit": f"/exhibit/{tag}", "manifest": f"/manifest/{tag}",
                "elapsed_ms": int((time.time() - t0) * 1000), "via": via})
    yield _sse({"type": "done"})


@app.post("/run")
async def run(request: Request,
              mode: str = Form("scenario"),
              tier: str = Form("flash"),          # flash | opus | recorded
              scenario: str = Form("clean"),
              request_text: str = Form(""),
              dec_page: Optional[UploadFile] = File(None)):
    ip = request.client.host if request.client else "?"
    if not _rate_ok(ip):
        return JSONResponse({"error": "rate limit: 20 runs/hour"}, status_code=429)
    if scenario not in SCENARIO_POLICY:
        return JSONResponse({"error": "unknown scenario"}, status_code=400)

    policy_path = str(DATA / SCENARIO_POLICY[scenario])
    req_text = (DATA / "coi_request.txt").read_text()

    if mode == "upload":
        if not dec_page:
            return JSONResponse({"error": "upload mode needs a dec_page PDF"}, status_code=400)
        raw = await dec_page.read()
        if not raw[:5] == b"%PDF-":
            return JSONResponse({"error": "That file isn't a PDF. Upload a PDF declarations page."}, status_code=400)
        if len(raw) > 10 * 1024 * 1024:
            return JSONResponse({"error": "File too large (max 10 MB)."}, status_code=400)
        dest = UPLOADS / f"{uuid.uuid4().hex[:8]}_{Path(dec_page.filename).name}"
        dest.write_bytes(raw)
        policy_path = str(dest)
    elif mode == "custom":
        req_text = request_text.strip() or (DATA / "coi_request.txt").read_text()

    return StreamingResponse(
        _stream(mode, tier, scenario, policy_path, req_text),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/exhibit/{tag}")
def exhibit(tag: str):
    p = OUT / f"loss_notice_{tag}.pdf"
    return FileResponse(p, media_type="application/pdf") if p.exists() \
        else JSONResponse({"error": "not found"}, status_code=404)

@app.get("/manifest/{tag}")
def manifest(tag: str):
    p = OUT / f"manifest_{tag}.json"
    return FileResponse(p, media_type="application/json") if p.exists() \
        else JSONResponse({"error": "not found"}, status_code=404)

# --- source documents, so the demo can SHOW the inputs -----------------------
CORPUS = {"policy-inforce": ("coi_policy_inforce.pdf", "application/pdf"),
          "policy-expired": ("coi_policy_expired.pdf", "application/pdf"),
          "request": ("coi_request.txt", "text/plain"),
          "config": ("agency_config.json", "application/json")}

@app.get("/corpus/{name}")
def corpus(name: str):
    if name not in CORPUS:
        return JSONResponse({"error": "unknown document"}, status_code=404)
    fname, mtype = CORPUS[name]
    return FileResponse(DATA / fname, media_type=mtype)


@app.get("/health")
def health():
    import os
    return {"ok": True,
            "keys": {"gemini": bool(os.environ.get("GEMINI_API_KEY")),
                     "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY"))}}

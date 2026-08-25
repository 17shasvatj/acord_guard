# Running ACORD Guard locally

## 1. Backend
    pip install -r requirements.txt
    export GEMINI_API_KEY=...        # optional; without keys, use the "Recorded" tier
    export ANTHROPIC_API_KEY=...
    uvicorn service:app --port 8000

## 2. Frontend (separate terminal)
    cd frontend && python3 -m http.server 8080
    open http://localhost:8080

The frontend talks to http://localhost:8000 by default. To point it elsewhere
(e.g. a deployed backend), in the browser console:
    localStorage.setItem("ACORD_API","https://your-backend.onrender.com")

## Tiers
  Gemini Flash / Opus 5 — live model calls (need keys)
  Recorded              — replays a captured run, no keys; also the auto-fallback

## Deploy
  Backend  → Render/Railway: `uvicorn service:app --host 0.0.0.0 --port $PORT`,
             set the two API keys as env vars, add a keep-warm ping.
  Frontend → Vercel/Netlify: deploy the frontend/ folder; set ACORD_API to the backend URL.

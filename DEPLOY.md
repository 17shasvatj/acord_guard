# Deploying ACORD Guard

Two pieces: FastAPI backend (Render) + static frontend (Vercel).

## 0. Push to GitHub (once)
    cd acord_guard
    git init && git add -A && git commit -m "ACORD Guard"
    # create an empty repo at github.com/<you>/acord-guard, then:
    git remote add origin https://github.com/<you>/acord-guard.git
    git branch -M main && git push -u origin main

Add a .gitignore first (below) so junk/keys don't get committed.

## 1. Backend on Render
1. render.com -> New -> Web Service -> connect the GitHub repo.
2. Render auto-reads render.yaml. Confirm: build = pip install + build_corpus,
   start = uvicorn ... --port $PORT.
3. In the dashboard, set the two secret env vars:
      GEMINI_API_KEY = ...
      ANTHROPIC_API_KEY = ...
4. Deploy. When live, note the URL, e.g. https://acord-guard-api.onrender.com
5. Test it:  curl https://acord-guard-api.onrender.com/health   -> keys true
             curl -N -X POST https://acord-guard-api.onrender.com/run \
                  -F tier=recorded -F scenario=expired            -> streams to BLOCKED

## 2. Frontend on Vercel
1. Edit frontend/index.html: set  const API_BASE = "https://<your-render-url>";
   (search for "CHANGE THIS FOR DEPLOY")
2. Commit + push.
3. vercel.com -> New Project -> import the repo -> Root Directory = "frontend"
   -> Framework Preset = "Other" (it's static) -> Deploy.
4. Open the Vercel URL. Run the "In-force policy" scenario on the "Saved run"
   tier first (no keys needed) to confirm the wiring, then a live tier.

## 3. Before you send the link
- [ ] Backend /health shows both keys true
- [ ] Frontend API_BASE points at the Render URL (not localhost)
- [ ] All three scenarios x recorded tier work from the deployed frontend
- [ ] One live Opus run works from the deployed frontend
- [ ] Upload mode works with a real dec-page PDF
- [ ] If on Render free tier: hit the backend once ~1 min before the demo (cold start)

## Cold starts (free tier only)
First request after ~15 min idle spins up for ~45s. Either use the Starter plan
(always-on) for the demo window, or ping the backend yourself just before sharing.

# ACORD Guard — frontend

Single self-contained `index.html` — no build step, no dependencies, no framework.
All markup, CSS, and JS are in the one file.

## Run locally
    cd frontend
    python3 -m http.server 8080
    open http://localhost:8080

It talks to the backend at `http://localhost:8000` by default. To point it at a
deployed backend, open the browser console and run:

    localStorage.setItem("ACORD_API", "https://your-backend.onrender.com")

(then reload). The current API base is shown in the page footer.

## Deploy
Deploy this folder as a static site (Vercel, Netlify, GitHub Pages, or any static host).
Set `ACORD_API` via the console line above, or hardcode the `API` constant near the
top of the `<script>` block in `index.html` before deploying.

## What it does
Four sections: (1) Gail's invented-vs-refused evidence, (2) their code that checks
shape not truth, (3) the live Guard runner — streams pipeline stages and ops events
(including model 503 retries) over SSE, opens the verified PDF + audit manifest, and
(4) the "feeds your existing skill" fit note. Three input modes: curated scenarios,
upload-your-own-dec-page, and custom free-text request. The "Recorded" tier runs with
no API keys and is also the automatic fallback shown when a live model call fails.

# Resume-Builder

AI – LLM-Powered Personalized Resume Builder

Ingests your career sources (CV `.docx`/`.pdf`, LinkedIn data export, GitHub
profile, free text) into one canonical **career profile**, then — given any job posting — generates a
**tailored CV** that emphasizes relevant experience **without fabricating
anything**. Every generated claim is traced back to a source document; anything
that can't be traced is flagged for your review instead of silently shipped.

## How it works

Two LangGraph pipelines behind a FastAPI service:

```mermaid
flowchart LR
    subgraph Ingestion
        A[CV / LinkedIn / GitHub / free text] --> B[extract per source<br/>Haiku] --> C[synthesize profile<br/>Sonnet] --> D[(versioned JSON store)]
    end
    subgraph Tailoring
        D --> E[analyze job post] --> F[tailor CV<br/>no-fabrication rules] --> G[validation gate<br/>source map + similarity + LLM check] --> R[human review<br/>pauses on flags] --> H[render .docx / PDF<br/>+ optional cover letter]
    end
```

- **Traceability** — every bullet/skill maps back to the document it came from.
- **Conflict surfacing** — when sources disagree (e.g. two start dates), the
  conflict is returned to you, never silently resolved.
- **Validation gate** — tailored claims that can't be traced to your profile
  come back as `needs_review` flags.
- **Human-in-the-loop** — a flagged run *pauses* before rendering (LangGraph
  `interrupt()`), and anything you don't approve is removed from the CV. There
  is a three-panel review UI, served from the same container.

## Setup

Requires Python 3.11+ and an Anthropic API key.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in ANTHROPIC_API_KEY
```

Or with Docker (single container, profiles persisted in `./data`):

```bash
cp .env.example .env      # fill in ANTHROPIC_API_KEY
docker compose up --build
```

## Usage

Start the server (`uvicorn src.api.main:app --reload` locally, or the Docker
command above), then open `http://localhost:8000/` for the review UI (built
into the Docker image; locally run `cd frontend && npm install && npm run
build` first, otherwise `/` redirects to `/docs`). Or use curl:

> To access from another machine on your network, bind to all interfaces:
> `uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000`
> (Docker Compose already does this.)

```bash
# 1. Build your career profile from any mix of sources
curl -F "cv=@resume.docx" -F "github_username=your-gh-user" \
     -F "linkedin_export=@Basic_LinkedInDataExport.zip" \
     -F "free_text=I also mentor junior developers." \
     localhost:8000/ingest
# -> {"profile_id": "...", "version": 1, "profile": {... "conflicts": [...]}}

# 2. (Optional) review/edit the profile — each save creates a new version
curl localhost:8000/profile/<profile_id>
curl -X PUT localhost:8000/profile/<profile_id> -H 'content-type: application/json' -d @edited-profile.json

# 3. Tailor a CV for a job post
curl -X POST localhost:8000/tailor -H 'content-type: application/json' \
     -d '{"profile_id": "<profile_id>", "job_post": "<paste the job posting>"}'
# -> tailored_cv + validation.flags (review anything with needs_review: true)

# 4. Same call, but also render the documents and write a cover letter
curl -X POST localhost:8000/tailor -H 'content-type: application/json' \
     -d '{"profile_id": "<profile_id>", "job_post": "<paste the job posting>",
          "render": true, "cover_letter": true}'
# -> ... + tailor_id + documents[]
#    ...or "review_required": true + review.items — the run paused, nothing was
#    written. Decide per item and the same run continues (no re-tailoring):
curl -X POST "localhost:8000/tailor/<tailor_id>/resume" \
     -H 'content-type: application/json' \
     -d '{"approvals": {"flag-0": true, "flag-1": false}}'
# anything you don't approve is removed from the CV

curl -o cv.docx "localhost:8000/document/<tailor_id>"
curl -o cv.pdf  "localhost:8000/document/<tailor_id>?format=pdf"
```

PDF output needs LibreOffice (shipped in the Docker image); without it you get
the `.docx` and a warning in the log.

## Running the review UI in development

The API serves the built UI at `/`, so this is only needed when working on the
frontend itself:

```bash
cd frontend && npm install
npm run dev                                     # http://localhost:5173
UI_HOST=0.0.0.0 UI_PORT=3000 npm run dev        # reachable from another machine
API_URL=http://192.168.0.212:8000 npm run dev   # API on a different host
```

`UI_HOST`/`UI_PORT` set where the Vite server listens; `API_URL` sets where it
proxies the API calls. Details in
[OPERATIONS.md](OPERATIONS.md#developing-the-ui-against-a-running-api).

## Tests

```bash
pytest tests/unit/ -v        # unit tests — all LLM calls mocked, no network
pytest -m integration        # end-to-end against the real Anthropic API
cd frontend && npm test      # review UI — vitest, no API needed
```

## Documentation

| Doc | Contents |
|---|---|
| [PRODUCT-GUIDE.md](PRODUCT-GUIDE.md) | User flows, guardrails, current limitations |
| [OPERATIONS.md](OPERATIONS.md) | Full setup, environment variables, deployment |
| [API-REFERENCE.md](API-REFERENCE.md) | Endpoint reference (REST + SSE) |
| [TECHNICAL-DESIGN.md](TECHNICAL-DESIGN.md) | Architecture and agent design |
| [PLAN.md](PLAN.md) | Phase roadmap (LinkedIn ingest, document rendering, review UI) |
| [HISTORY.md](HISTORY.md) | Change log |

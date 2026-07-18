# Implementation Plan — Career Profile & Targeted CV Generator (all phases)

## Context

The repo is a fresh scaffold containing only `TECHNICAL-DESIGN.md`, which specifies a two-stage LangGraph agent pipeline: (1) ingest career sources (CV docx/PDF, GitHub, LinkedIn export, free text) into a canonical `CareerProfile`; (2) given a job post, generate a tailored CV (optionally a cover letter) with an anti-fabrication validation gate, rendered to docx/PDF, reviewed by a human before final output. No source code exists yet.

Decisions agreed with the user:
- **FastAPI from day one**, all agents in **one Docker container**.
- **Storage: versioned JSON files** (single-user), no Postgres.
- Phasing follows the design doc's build order (§12): core pipeline first, then LinkedIn, rendering, and UI as subsequent phases — **this plan covers all phases through the complete design doc**.

Branch per phase: `feat/core-pipeline`, `feat/linkedin-ingest`, `feat/document-render`, `feat/review-ui` (never commit to main).

## Architecture decisions (apply to all phases)

- **Two LangGraph `StateGraph`s** sharing one schema module, since the stages run at different times:
  - **Ingestion graph:** `ingest_sources → extract_source (per source) → synthesize_profile → store_profile`
  - **Tailoring graph:** `analyze_job → tailor_cv → validate_cv` (Phase 3 adds `render_document`; Phase 4 adds a human-review `interrupt()` before it)
  - State is a `TypedDict` at the top of each graph file; node names are verbs.
- **Deterministic parsing before LLM**: docx/PDF/GitHub/LinkedIn ingestion nodes are pure Python. LLM calls happen only in extraction, synthesis, job-analysis, tailoring, validation.
- **Model tiering** (env-configurable defaults): Haiku (`claude-haiku-4-5-20251001`) for per-source extraction; Sonnet (`claude-sonnet-5`) for synthesis, job analysis, tailoring; validation defaults to Sonnet with env override to Opus (`claude-opus-4-8`).
- **Structured output**: every LLM node uses `ChatAnthropic(...).with_structured_output(<PydanticModel>)` — no free-form JSON parsing.
- **Traceability**: every `Experience`/`Project`/`Skill` carries `source`; synthesis populates `raw_source_map` (claim → source doc id). Cross-source conflicts go into a `CareerProfile.conflicts` list — surfaced, never silently resolved.
- **Validation gate**: (a) non-LLM check — every `TailoredCV` bullet/skill must map to a profile entry via `raw_source_map`, with difflib similarity vs. the original bullet; (b) LLM cross-check on anything below threshold. Flags returned as `needs_review`. In Phases 1–3 human review is client-side; Phase 4 upgrades it to a LangGraph `interrupt()` with checkpointer, per design doc §8.

## File layout (end state)

```
requirements.txt, .env.example, Dockerfile, docker-compose.yml, PLAN.md
src/
├── config.py                  # env loading (python-dotenv), model names, data dir
├── models/schemas.py          # SourceDocument, Experience, Project, Skill, CareerProfile,
│                              # JobRequirements, TailoredCV, ValidationResult, CoverLetter
├── tools/
│   ├── docx_reader.py         # python-docx, headers as section boundaries
│   ├── pdf_reader.py          # pdfplumber; per-page extraction; two-column caveat documented
│   ├── github_client.py       # httpx vs api.github.com: repos, languages, README excerpts (truncated)
│   ├── linkedin_export.py     # Phase 2: parse official data-export ZIP (Positions/Education/Skills/Certifications CSVs)
│   └── docx_renderer.py       # Phase 3: python-docx template rendering (+ PDF via LibreOffice headless)
├── agents/
│   ├── ingestion_graph.py     # IngestionState TypedDict + StateGraph wiring
│   ├── tailoring_graph.py     # TailoringState TypedDict + StateGraph wiring (+checkpointer in Phase 4)
│   ├── extraction.py          # extract_source node (Haiku)
│   ├── synthesis.py           # synthesize_profile node (Sonnet): dedupe, conflict surfacing, narrative
│   ├── job_analysis.py        # analyze_job node → JobRequirements
│   ├── tailoring.py           # tailor_cv node → TailoredCV (hard no-fabrication rules in system prompt)
│   ├── validation.py          # validate_cv node: source-map check + similarity + LLM cross-check
│   └── document.py            # Phase 3: render_document node (no LLM)
├── chains/prompts/            # one prompt-template module per LLM agent
├── utils/profile_store.py     # versioned JSON store: data/profiles/{profile_id}/v{n}.json + latest pointer
└── api/
    ├── main.py                # FastAPI app factory; serves built frontend in Phase 4
    └── routes.py
frontend/                      # Phase 4: React + Vite + TanStack
tests/
├── conftest.py                # fixtures: sample docx/pdf/LinkedIn-export files, mocked LLMs, tmp data dir
├── unit/                      # no real API calls; LLMs mocked
└── integration/               # @pytest.mark.integration; real Anthropic/GitHub APIs
data/profiles/                 # gitignored runtime storage
```

## Environment variables (`.env.example`, kept in sync every phase)

```
ANTHROPIC_API_KEY=sk-...
GITHUB_TOKEN=ghp-...            # optional, raises GitHub rate limits
EXTRACTION_MODEL=claude-haiku-4-5-20251001
SYNTHESIS_MODEL=claude-sonnet-5
TAILORING_MODEL=claude-sonnet-5
VALIDATION_MODEL=claude-sonnet-5
DATA_DIR=./data
```

---

## Phase 1 — Core pipeline + FastAPI + Docker (design doc §12 steps 1–4)

1. **Scaffold** — branch `feat/core-pipeline`; `.venv`; `requirements.txt` (langgraph, langchain-anthropic, fastapi, uvicorn, python-docx, pdfplumber, httpx, python-dotenv, pydantic, sse-starlette, pytest, pytest-mock, pytest-asyncio); `.env.example`; verify `.gitignore` covers `.env`, `.venv/`, `data/`.
2. **Schemas** — `src/models/schemas.py` exactly per design doc §4/§6/§7, plus `ValidationResult` and `CareerProfile.conflicts`.
3. **Ingestion tools** — docx/pdf readers, GitHub client, free-text passthrough → `SourceDocument` objects.
4. **Extraction + synthesis agents**, ingestion graph wiring, `profile_store`.
5. **Job analysis, tailoring, validation agents**, tailoring graph wiring.
6. **FastAPI layer + Docker** — endpoints below; SSE via in-process job registry + `asyncio.Queue`; `Dockerfile` (python:3.11-slim, uvicorn on `0.0.0.0`) + `docker-compose.yml` (one service, `.env`, `data/` volume).

| Endpoint | Behavior |
|---|---|
| `POST /ingest` | multipart: CV file(s) + optional `github_username` + optional `free_text` → runs ingestion graph → `profile_id` + `CareerProfile` (incl. `conflicts`) |
| `GET /ingest/{job_id}/events` | SSE per-node progress |
| `GET /profile/{profile_id}` | latest version; `?version=n` for specific |
| `PUT /profile/{profile_id}` | save user-edited profile as new version (v1 conflict resolution) |
| `POST /tailor` | `profile_id` + job post text → `TailoredCV` + `ValidationResult` |
| `GET /healthz` | liveness |

**Tests:** `tests/unit/` — schemas, docx/pdf readers (fixture files), github client (mocked httpx), extraction/synthesis (mocked LLM; dedupe + conflict logic), tailoring (subset-of-profile invariant), **validation (key suite: fabricated bullet → flagged; reworded-but-sourced → passes; unsourced skill → flagged)**, profile_store versioning, API via `TestClient` with graphs mocked. `tests/integration/test_pipeline.py` — real end-to-end on a sample CV.

## Phase 2 — LinkedIn export ingestion (design doc §12 step 5)

1. `src/tools/linkedin_export.py` — accept the official LinkedIn data-export ZIP (or individual CSVs): parse Positions, Education, Skills, Certifications, Recommendations into `SourceDocument`s with `structured_fields`. **No scraping** (ToS), exactly per design doc §3.
2. Extend `POST /ingest` to accept a `linkedin_export` ZIP upload; register the source in the ingestion graph (extraction prompt gains a LinkedIn variant that trusts `structured_fields` over raw text).
3. Synthesis already handles multi-source dedupe/conflicts — extend unit tests to cover LinkedIn-vs-CV conflicts (same job, different dates → appears in `conflicts`).

**Tests:** `tests/unit/test_linkedin_export.py` (fixture ZIP/CSVs), extended `test_synthesis.py` conflict cases, extended `test_api.py` upload case.

## Phase 3 — Document Agent: rendering + cover letter (design doc §9, §1)

1. `src/tools/docx_renderer.py` + `src/agents/document.py` — pure rendering (no LLM) of `TailoredCV` → `.docx` from a bundled template (name/contact header, summary, experiences, projects, skills); PDF via `libreoffice --headless` in the Docker image (add to Dockerfile).
2. Add `render_document` node to the tailoring graph after `validate_cv` (skipped when validation flags exist and caller hasn't approved).
3. **Cover letter (optional output per design doc §1):** `generate_cover_letter` prompt in tailoring agent, same no-fabrication constraints, returns `CoverLetter` schema; rendered by the same document agent.
4. API: `POST /tailor` gains `render: bool` + `cover_letter: bool` flags; new `GET /document/{tailor_id}` returns the .docx/.pdf file.

**Tests:** `tests/unit/test_docx_renderer.py` (render fixture `TailoredCV`, re-open with python-docx and assert content/ordering), `test_document_agent.py` (skip-on-flags behavior), cover-letter prompt unit test (mocked LLM), extended API tests. PDF conversion covered by an integration test (needs LibreOffice, runs in Docker).

## Phase 4 — Review UI + human-in-the-loop (design doc §10 frontend, §8 interrupt, §11 guardrails)

1. `frontend/` — React + Vite + TanStack Query. Three-panel flow per design doc §10:
   - **Sources panel:** upload CV/LinkedIn ZIP, GitHub username, free text; live SSE progress.
   - **Profile panel:** review/edit `CareerProfile`, resolve `conflicts` explicitly (writes new version via `PUT /profile/{id}`).
   - **Tailor panel:** paste job post → side-by-side diff of original vs. tailored bullets, `needs_review` flags highlighted, approve/reject each flagged item, then trigger render + download.
2. **Human review checkpoint server-side:** add a `MemorySaver` checkpointer to the tailoring graph and an `interrupt()` between `validate_cv` and `render_document`; new endpoints `GET /tailor/{tailor_id}/review` (pending flags) and `POST /tailor/{tailor_id}/resume` (approvals in → graph resumes to render). This completes design doc §11: no CV is rendered without the person seeing flagged items.
3. Serve the built frontend from FastAPI (`StaticFiles`) so it stays one container; multi-stage Dockerfile (node build stage → python runtime stage).

**Tests:** frontend unit tests with vitest (panel state, diff view, flag approval flow); backend `test_review_flow.py` — interrupt fires on flags, resume renders, no-flag runs skip straight to render; API tests for the two new endpoints.

---

## Docs sync (every phase, mandatory per CLAUDE.md)

- `TECHNICAL-DESIGN.md` — "Implementation notes" section per phase (two-graph split, JSON storage, interrupt design in Phase 4) + Mermaid diagrams of implemented graphs.
- `OPERATIONS.md` (new in Phase 1) — setup, env-vars table, docker/test commands; updated when Phase 3 adds LibreOffice and Phase 4 adds the frontend build.
- `API-REFERENCE.md` (new in Phase 1) — every endpoint added/changed per phase.
- `PRODUCT-GUIDE.md` (new in Phase 1) — user flows and current limitations, updated as phases remove limitations.
- `HISTORY.md` (new in Phase 1) — one table entry per change, newest first, required format.
- Project `CLAUDE.md` — replace "no code exists" section with real run/test commands in Phase 1.

## Test policy (per mandatory rules, applies to every phase)

1. **All existing tests pass** — each phase must leave the previous phases' suites green; no regressions.
2. **Removed tests announced** — none anticipated; if a phase obsoletes a test (e.g. Phase 4 replaces client-side review tests), it is named and the reason recorded in `HISTORY.md` before removal.
3. **New tests announced** — listed per phase above; every new code path ships with unit coverage, LLM calls mocked in unit tests, real-API paths under `@pytest.mark.integration`.
4. **Tests documented** — added/removed test files recorded in `HISTORY.md` and commands in `OPERATIONS.md`/`CLAUDE.md`.

## Verification (end of each phase)

- `pytest tests/unit/ -v` green; `pytest -m integration` with real keys as final gate.
- **Phase 1:** `docker compose up --build`; `curl -F cv=@sample.docx -F github_username=<user> localhost:8000/ingest` → inspect `CareerProfile` + `data/profiles/`; `POST /tailor` → confirm output only contains profile-sourced facts and an intentionally injected fake skill gets flagged.
- **Phase 2:** ingest a real LinkedIn export ZIP alongside a CV → conflicting dates appear in `conflicts`, not silently merged.
- **Phase 3:** tailor with `render=true` → open the .docx/.pdf, verify structure matches `TailoredCV` ordering.
- **Phase 4:** full browser flow from the Windows host (server on `0.0.0.0`): upload → edit profile → tailor → approve a flagged item → download rendered CV; verify a flagged run cannot render without approval.

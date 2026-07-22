# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mandatory Rules for All Changes

### Documentation must be kept in sync with every change

Any code change, configuration change, or architectural decision MUST update the following files accordingly
:

| File | When to Update | What to Record |
|---|---|---|
| `TECHNICAL-DESIGN.md` | Architecture changes, new components, technology stack changes, data flow changes,
 schema changes | Updated design rationale, component interactions, technology descriptions, system/data/wor
kflow diagrams, database schema |
| `OPERATIONS.md` | Setup changes, deployment changes, environment variables added/removed, external depende
ncy changes | Updated installation steps, env vars table, deployment procedures, operational commands |
| `PRODUCT-GUIDE.md` | Feature changes, business flow changes, user-facing behavior changes | Updated produc
t capabilities, business flows, user scenarios |
| `API-REFERENCE.md` | Tool/resource/notification added/modified/removed, parameter changes | Updated tool s
ignatures, resource URIs, notification formats, parameter schemas, return types |
| `HISTORY.md` | **Every** change (code, config, architecture) | Date \| Action \| Goal \| Root Cause \| Imp
lementation Detail \| Related Files \| Test Coverage |

### Rules of engagement

1. **Before** starting any implementation task, review the existing docs in these files to ensure you unders
tand the current state.
2. **After** completing any change, update all applicable documentation files before marking the task done.
3. If a change does not affect a particular doc file, leave a one-line note explaining why (e.g., "No API ch
ange — internal refactor only" in API-REFERENCE.md).
4. HISTORY.md entries go at the **top** (newest first) with format: `| YYYY-MM-DD | Action | Goal | Root Cau
se | Implementation Detail | Related Files | Test Coverage |`
5. Never delete or rewrite past entries in HISTORY.md — only append new ones.
6. Diagrams in TECHNICAL-DESIGN.md should use Mermaid markdown syntax (rendered automatically by GitHub).

## Project status

Phase 1 (core pipeline + FastAPI + Docker), Phase 2 (LinkedIn data-export ingestion) and Phase 3 (document rendering + cover letter) are implemented — see `PLAN.md` for the phase roadmap and `TECHNICAL-DESIGN.md` §13 for implementation notes. The system is an **AI/LLM-powered personalized resume builder**: two LangGraph `StateGraph`s (ingestion and tailoring) behind a FastAPI service, with an anti-fabrication validation gate, a versioned JSON profile store, and `.docx`/PDF rendering gated on that validation.

## Commands

```bash
# setup (Python 3.11 venv)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in ANTHROPIC_API_KEY

# run the API locally
uvicorn src.api.main:app --reload

# run in Docker (one container, data/ volume)
docker compose up --build

# tests
pytest tests/unit/ -v    # unit tests — all LLMs mocked, no network
pytest -m integration    # real-API end-to-end (needs ANTHROPIC_API_KEY)
```

Unit tests run by default (`pytest.ini` excludes the `integration` marker). Mock LLM calls by monkeypatching `make_llm` in the specific agent module (each agent imports it by name), e.g. `monkeypatch.setattr(src.agents.extraction, "make_llm", ...)`.

## Conventions to follow when adding code

The user's global Claude Code instructions (`~/.claude/CLAUDE.md`) define the default conventions for their Python/LangChain agent projects, and apply here unless this file says otherwise:

- Python 3.11+, developed in a `.venv`, dependencies tracked in `requirements.txt`.
- Project layout: `src/agents/`, `src/chains/`, `src/tools/`, `src/models/`, `src/utils/`, with `tests/unit/` and `tests/integration/` (pytest).
- Type hints and Google-style docstrings on public functions/classes; Pydantic models for structured agent I/O instead of raw dicts.
- LangGraph state as a typed `TypedDict`; node names are verbs (`retrieve_context`, `generate_response`); use a `checkpointer` for graphs needing cross-turn memory.
- Secrets via `.env` + `python-dotenv`, never hardcoded or committed.
- Work on feature branches (`feat/...`, `fix/...`, `test/...`); never commit directly to `main`.

Confirm the intended architecture (e.g. is this a LangGraph agent, a simple pipeline, a web app with a resume-parsing/generation backend?) before scaffolding `src/`, since the repo doesn't yet indicate which shape the resume builder will take.

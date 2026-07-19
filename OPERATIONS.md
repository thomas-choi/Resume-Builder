# Operations Guide

## Prerequisites

- Python 3.11+ (local development)
- Docker + Docker Compose (deployment)
- An Anthropic API key

## Local setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY (and optionally GITHUB_TOKEN)
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes* | — | LLM calls with the default `anthropic` provider |
| `GITHUB_TOKEN` | no | — | Raises GitHub API rate limits for `github_username` ingestion |
| `LLM_PROVIDER` | no | `anthropic` | Provider switch (same method as FUND `get_llm`): `anthropic`, `openai`, `google`, `nvidia`, `llamacpp`, `deepseek`, `openrouter`. Non-Anthropic providers need their `langchain-*` package installed and `*_MODEL` vars set to that provider's model ids. |
| `LLM_API_KEY` | no | falls back to `ANTHROPIC_API_KEY` | Provider API key (*required if `LLM_PROVIDER` isn't `anthropic`) |
| `LLM_TEMPERATURE` | no | unset | Sampling temperature; leave unset for current Claude models (they reject non-default sampling params) |
| `LLM_MAX_TOKENS` | no | `8000` | Default output token cap per LLM call |
| `LLM_BASE_URL` | no | — | Provider base URL override (e.g. local llama.cpp) |
| `LLM_STREAM_TIMEOUT_S` | no | `90` | Max seconds of provider-client inactivity before abort |
| `EXTRACTION_MODEL` | no | `claude-haiku-4-5-20251001` | Per-source extraction model |
| `SYNTHESIS_MODEL` | no | `claude-sonnet-5` | Profile synthesis model |
| `TAILORING_MODEL` | no | `claude-sonnet-5` | Job analysis + CV tailoring model |
| `VALIDATION_MODEL` | no | `claude-sonnet-5` | Anti-fabrication LLM cross-check (override to `claude-opus-4-8` for max precision) |
| `DATA_DIR` | no | `./data` | Root of the versioned JSON profile store |
| `VALIDATION_SIMILARITY_THRESHOLD` | no | `0.55` | difflib ratio below which a tailored bullet triggers the LLM cross-check |

Secrets live in `.env` (gitignored) and are loaded via `python-dotenv`; never
commit or hardcode them.

## Running the server

Local:

```bash
uvicorn src.api.main:app --reload
```

Docker (one service, `.env` file, `data/` volume):

```bash
docker compose up --build
# server listens on 0.0.0.0:8000
```

## Testing

```bash
pytest tests/unit/ -v        # unit tests — no network, all LLMs mocked
pytest -m integration        # end-to-end against real Anthropic API (needs ANTHROPIC_API_KEY)
```

Unit tests are the default (`pytest.ini` excludes the `integration` marker).

## Smoke test

```bash
curl localhost:8000/healthz
curl -F "cv=@resume.docx" -F "github_username=<user>" localhost:8000/ingest
curl -X POST localhost:8000/tailor -H 'content-type: application/json' \
  -d '{"profile_id": "<id from ingest>", "job_post": "<paste job post>"}'
```

Profiles land in `data/profiles/{profile_id}/v{n}.json` with a `latest`
pointer file.

## Data management

- Storage is plain versioned JSON — back up by copying `data/`.
- Deleting a profile = deleting its directory; there is no API for deletion yet.
- Profile versions are append-only; `PUT /profile/{id}` always creates a new version.

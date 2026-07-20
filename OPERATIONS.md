# Operations Guide

## Prerequisites

- Python 3.11+ (local development)
- Docker + Docker Compose (deployment)
- An Anthropic API key (default provider) — or a local llama.cpp server (see [Local LLM via llama.cpp](#local-llm-via-llamacpp))

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
| `LLM_PROVIDER` | no | `anthropic` | Provider switch (same method as FUND `get_llm`): `anthropic`, `openai`, `google`, `nvidia`, `llamacpp`, `deepseek`, `openrouter`. Non-Anthropic providers need their `langchain-*` package installed and `*_MODEL` vars set to that provider's model ids. Packages for `anthropic`, `openai`, `google`, `nvidia`, and `llamacpp` ship in `requirements.txt`; `deepseek`/`openrouter` need a manual `pip install`. For `deepseek` the factory disables thinking mode per request — DeepSeek thinking models reject the forced tool call that structured output requires. |
| `LLM_API_KEY` | no | falls back to `ANTHROPIC_API_KEY` | Provider API key (*required if `LLM_PROVIDER` isn't `anthropic`) |
| `LLM_TEMPERATURE` | no | unset | Sampling temperature; leave unset for current Claude models (they reject non-default sampling params). For local/OSS providers set ~`0.2` — low temperature keeps the extraction and validation stages factual, which the anti-fabrication gate depends on |
| `LLM_MAX_TOKENS` | no | `8000` | Default output token cap per LLM call |
| `LLM_BASE_URL` | no | — | Provider base URL override (e.g. local llama.cpp) |
| `LLM_STREAM_TIMEOUT_S` | no | `90` | Max seconds of provider-client inactivity before abort. Raise to `300` for local llama.cpp — an 8k-token synthesis output on local hardware can legitimately exceed 90 s |
| `EXTRACTION_MODEL` | no | `claude-haiku-4-5-20251001` | Per-source extraction model |
| `SYNTHESIS_MODEL` | no | `claude-sonnet-5` | Profile synthesis model |
| `TAILORING_MODEL` | no | `claude-sonnet-5` | Job analysis + CV tailoring model |
| `VALIDATION_MODEL` | no | `claude-sonnet-5` | Anti-fabrication LLM cross-check (override to `claude-opus-4-8` for max precision) |
| `DATA_DIR` | no | `./data` | Root of the versioned JSON profile store |
| `LOG_LEVEL` | no | `INFO` | Root log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`); unknown values fall back to `INFO`. `DEBUG` traces the ingestion pipeline: GitHub repo list + full source document, extraction inputs/results, synthesis payload/profile (noisy libs — httpx/httpcore/urllib3/watchfiles/openai — stay capped at INFO) |
| `LOG_FILE` | no | unset | Log file path (e.g. `./logs/app.log`); unset = console only. Rotates at 10 MB keeping 3 backups; parent dir auto-created; uvicorn access/error logs are routed there too |
| `VALIDATION_SIMILARITY_THRESHOLD` | no | `0.55` | difflib ratio below which a tailored bullet triggers the LLM cross-check |

Secrets live in `.env` (gitignored) and are loaded via `python-dotenv`; never
commit or hardcode them.

## Running the server

Local (loopback only — same machine):

```bash
uvicorn src.api.main:app --reload
```

Local, reachable from other hosts on the network (e.g. a Windows browser
hitting `http://<this-machine-ip>:8000/docs`):

```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Docker (one service, `.env` file, `data/` volume):

```bash
docker compose up --build
# server listens on 0.0.0.0:8000
```

## Local LLM via llama.cpp

The app can run fully offline against a local `llama-server` by setting
`LLM_PROVIDER=llamacpp` and `LLM_BASE_URL=http://<host>:8080/v1`. This section
records the reference setup for the current dev box (RTX 3090 24 GB VRAM,
32 GB system RAM) running **Qwen3.6-35B-A3B-UD-Q4_K_M.gguf** (MoE, ~3B active
params), and the reasoning behind each flag.

### Server command

```bash
llama-server -m Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  -c 32768 -ngl 99 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --jinja \
  --host 0.0.0.0 --port 8080
```

### Why these flags (VRAM budget)

A 35B MoE at Q4_K_M is ~20 GB of weights, so it fits fully on a 24 GB GPU but
leaves only ~4 GB for KV cache and compute buffers. Every flag exists to make
32k context fit inside that headroom:

| Flag | Reasoning |
|---|---|
| `-c 32768` | The context must hold **prompt + generated output**. llama.cpp defaults to a small `n_ctx` (4096 in older builds), which the ~5k-token GitHub extraction prompt alone overflows (`exceed_context_size_error`). Synthesis is bigger still — it feeds all merged extractions back in — and `LLM_MAX_TOKENS=8000` of output must also fit, so size the context well past the largest prompt, not just past the first error. |
| `-ngl 99` | Offload every layer to the GPU; the quantized model fits, so nothing should run on CPU by default. |
| `-fa on -ctk q8_0 -ctv q8_0` | Flash attention plus 8-bit KV cache halves KV memory at 32k so it fits in the ~4 GB left after weights. Flash attention is required for V-cache quantization. |
| `--jinja` | Uses the model's real chat template. Qwen3 needs this for reliable JSON/structured output, and every pipeline stage relies on `with_structured_output`. |
| `--host 0.0.0.0` | Only needed when the API server runs on a different machine than llama-server; harmless otherwise. |

### If it doesn't fit (OOM at startup)

1. **First** move MoE expert weights to system RAM: `--n-cpu-moe 8` (increase
   until it loads; 32 GB RAM is ample). Because only ~3B params are active per
   token, this costs far less throughput than offloading dense layers would.
2. **Then** as a last resort drop context to `-c 16384` — still enough for
   ingestion, but leaves less room for large synthesis prompts + 8k output.

Do **not** shrink the app inputs first (`MAX_REPOS` / `README_EXCERPT_CHARS`
in `src/tools/github_client.py`); that trades profile quality for a server
config problem.

### Model-specific note

If the GGUF is a *thinking* variant, its `<think>` blocks can conflict with
JSON-schema-constrained output (symptom: empty or mangled extractions). Add:

```bash
--chat-template-kwargs '{"enable_thinking":false}'
```

### Matching app config (`.env`)

```bash
LLM_PROVIDER=llamacpp
LLM_BASE_URL=http://localhost:8080/v1
LLM_TEMPERATURE=0.2        # llama.cpp accepts sampling params; low temp aids factuality
LLM_MAX_TOKENS=8000
LLM_STREAM_TIMEOUT_S=300   # local generation is slower than hosted APIs
EXTRACTION_MODEL=qwen3.6-35b-a3b   # llama-server ignores the name and serves its
SYNTHESIS_MODEL=qwen3.6-35b-a3b    # loaded model; set it anyway so logs record
TAILORING_MODEL=qwen3.6-35b-a3b    # the model actually used
VALIDATION_MODEL=qwen3.6-35b-a3b
```

`LLM_API_KEY` is not used by a local llama-server (any placeholder value works).

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
pointer file. Each `/ingest` call also returns a `run_id` and archives its
inputs + output copy (see [Run tracking & retention](#run-tracking--retention)):

```bash
RUN_ID=<run_id from ingest response>
ls data/sources/$RUN_ID          # cv/, github/github.json, linkedin/linkedin-summary.txt, manifest.json
cat data/output/$RUN_ID/output.json   # copy of the synthesized profile
```

## Data management

- Storage is plain versioned JSON — back up by copying `data/`.
- Deleting a profile = deleting its directory; there is no API for deletion yet.
- Profile versions are append-only; `PUT /profile/{id}` always creates a new version.

### Run tracking & retention

Every `/ingest` execution is tagged with a `run_id` (the same value as `job_id`)
and its inputs/outputs are archived under `DATA_DIR`:

| Path | Contents |
|---|---|
| `data/sources/{run_id}/cv/<original-name>` | Raw uploaded CV bytes, saved **before** parsing |
| `data/sources/{run_id}/github/github.json` | Serialized GitHub `SourceDocument` |
| `data/sources/{run_id}/linkedin/linkedin-summary.txt` | The `free_text` input (LinkedIn summary path) |
| `data/sources/{run_id}/manifest.json` | Index of inputs (category, filename, size, sha256) linked to `profile_id`/`version` |
| `data/output/{run_id}/output.json` | Copy of the synthesized profile |

- **Retention / privacy:** raw CVs are now **retained** on disk (previously they
  were parsed from a temp file and immediately deleted). Treat `data/sources/`
  as sensitive PII — it holds original résumés and pasted personal summaries.
  `data/` is gitignored; back it up and purge it per your retention policy.
  There is no API for deleting a run yet — remove `data/sources/{run_id}` and
  `data/output/{run_id}` directories manually.
- **Log correlation:** with `LOG_FILE` set, every pipeline log line is tagged
  `[run:<run_id>]`, so a single run is greppable across steps:
  `grep '\[run:<run_id>\]' logs/app.log`.
- **Disk growth:** sources + output accumulate per run; prune old `run_id`
  directories periodically.

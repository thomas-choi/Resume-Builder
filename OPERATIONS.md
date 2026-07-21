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
| `GITHUB_TOKEN` | no | — | Raises GitHub API rate limits **and** unlocks richer contribution data for `github_username` ingestion: with a token the client uses GraphQL `repositoriesContributedTo` (all-time, with description/language/stars) plus per-repo commit counts; without one it falls back to the REST merged-PR search. If the token belongs to **the very username being ingested** it additionally unlocks private org memberships and private repos (see "GitHub coverage" below); for any other username those endpoints are never used |
| `GITHUB_INCLUDE_CONTRIBUTIONS` | no | `true` | Kill switch for the extra search/GraphQL calls that find contributions to repos the user doesn't own. `false` → the source document degrades to owned + organization repos only |
| `GITHUB_MAX_EXTERNAL_REPOS` | no | `15` | How many external (contributed-to) repos to keep, ranked by contribution volume (merged PRs + commits), not recency |
| `GITHUB_INCLUDE_PRIVATE` | no | `true` | Whether private repos are ingested on the self-token path. Their names, descriptions and README excerpts then reach the extraction LLM and are stored under `data/sources/`. `false` → public repos only, while private **org membership** is still discovered |
| `GITHUB_MAX_CONTRIBUTION_PROBES` | no | `150` | Budget for the `GET /repos/{full}/commits?author=` probes that prove a non-owned repo was actually worked on. Repos beyond the budget are dropped, never assumed |
| `GITHUB_MAX_ORG_REPOS` | no | `20` | How many organization/collaborator repos to render, newest contribution first. Each organization keeps at least one repo before recency fills the rest, so an old employer is not evicted by a busy current one |
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
| `SKILLS_DIR` | no | `./skills` | Directory of per-agent `SKILL.md` reasoning skills (FUND skills mechanism). Prompt **content**, not secrets — ships in the image, safe to commit. A missing/empty dir degrades gracefully: each agent falls back to its inline prompt scaffolding and the pipeline still runs |
| `LOG_LEVEL` | no | `INFO` | Root log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`); unknown values fall back to `INFO`. `DEBUG` traces the ingestion pipeline: GitHub repo list + full source document, extraction inputs/results, synthesis payload/profile (noisy libs — httpx/httpcore/urllib3/watchfiles/openai — stay capped at INFO) |
| `LOG_FILE` | no | unset | Log file path (e.g. `./logs/app.log`); unset = console only. Rotates at 10 MB keeping 3 backups; parent dir auto-created; uvicorn access/error logs are routed there too |
| `VALIDATION_SIMILARITY_THRESHOLD` | no | `0.55` | difflib ratio below which a tailored bullet triggers the LLM cross-check |

Secrets live in `.env` (gitignored) and are loaded via `python-dotenv`; never
commit or hardcode them.

## GitHub coverage and rate limits

`github_username` ingestion collects three tiers, each labelled separately in
the source document (see TECHNICAL-DESIGN.md §3):

1. repos owned by the username,
2. repos owned by organizations the user belongs to or collaborates on **and has
   committed to**,
3. repos the user only **contributed** to (merged PRs / commits).

**Self-token vs. third-party token.** `GET /users/{u}/orgs` lists only *public*
organization memberships, and GitHub's default for a membership is private — so
a user who belongs to five organizations can look org-less. When `GITHUB_TOKEN`
belongs to the very username being ingested, the client instead uses the viewer
endpoints `GET /user/orgs` and `GET /user/repos?affiliation=owner,organization_member,collaborator`,
which see private memberships and private repos. Identity is checked with
`GET /user` on every run: a token for anyone else falls back to the public
endpoints, so a third party's private data can never be reached. Set
`GITHUB_INCLUDE_PRIVATE=false` to keep private repos out of the source document
while still discovering the memberships.

**Access is not contribution.** `affiliation=collaborator` returns every repo the
user was ever invited to — in practice mostly repos they never touched. Each
non-owned repo therefore has to prove a commit via
`GET /repos/{full}/commits?author={u}` before it is rendered; repos already
proven by the merged-PR search or the GraphQL commit counts skip the probe.

**Rate limits.** Unauthenticated: 60 core req/h and **10 search req/min**;
with `GITHUB_TOKEN`: 5000 core req/h and 30 search req/min. Each owned/org repo
costs 2 extra calls (languages + README) plus at most 1 commit probe; external
repos cost none. The merged-PR search is fetched as a single page. Every
degradation path logs a `WARNING` and keeps the ingest alive: a 403/429 on the
search drops the external section, and a 403/429 on a commit probe stops the
sweep and drops the remaining organization repos. Set
`GITHUB_INCLUDE_CONTRIBUTIONS=false` to skip the search/GraphQL calls entirely,
`GITHUB_MAX_CONTRIBUTION_PROBES` to bound the probes, and
`GITHUB_MAX_EXTERNAL_REPOS` / `GITHUB_MAX_ORG_REPOS` to bound the sections.

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
# with a LinkedIn data export (Settings → "Get a copy of your data")
curl -F "cv=@resume.docx" -F "linkedin_export=@Basic_LinkedInDataExport.zip" \
  localhost:8000/ingest
curl -X POST localhost:8000/tailor -H 'content-type: application/json' \
  -d '{"profile_id": "<id from ingest>", "job_post": "<paste job post>"}'
```

Profiles land in `data/profiles/{profile_id}/v{n}.json` with a `latest`
pointer file. Each `/ingest` call also returns a `run_id` and archives its
inputs + output copy (see [Run tracking & retention](#run-tracking--retention)):

```bash
RUN_ID=<run_id from ingest response>
ls data/sources/$RUN_ID          # cv/, github/github.json, linkedin/, manifest.json
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
| `data/sources/{run_id}/linkedin/<original-name>` | Uploaded LinkedIn data export (`.zip` / `.csv`), saved **before** parsing |
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
- **LinkedIn exports (Phase 2) — no setup change.** No new env vars and no new
  dependencies (`zipfile`/`csv` are stdlib); parsing is offline, so no outbound
  request and no credential is involved. Operationally the one thing to know is
  size and sensitivity: an export archive is retained verbatim under
  `data/sources/{run_id}/linkedin/` and can be tens of MB (it contains far more
  than the career sections the builder reads), so factor it into disk planning
  and treat it as PII along with the résumés. A rejected export (400) is
  archived too, on purpose — it is the artifact you need to diagnose the
  rejection. Parse decisions are logged at DEBUG
  (`linkedin[<file>]: parsed sections {...}`, plus one line per ignored
  archive member).
- **Partial extractions (Phase 1.e) — no setup change.** No new env vars or
  dependencies. Dropped items and skipped sources are logged at `WARNING`
  (`extract[<source_id>]: dropped projects[11] …`, `extraction failed for
  source …`), so a run that returns 200 with fewer entries than expected is
  diagnosable from the log: `grep 'extract\[' logs/app.log`.

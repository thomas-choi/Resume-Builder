# Operations Guide

## Prerequisites

- Python 3.11+ (local development)
- Docker + Docker Compose (deployment)
- **Node 20+ / npm** — only to build or develop the review UI (`frontend/`).
  Not needed for the API, and not needed for `docker compose` either: the image
  builds the UI in its own stage
- An Anthropic API key (default provider) — or a local llama.cpp server (see [Local LLM via llama.cpp](#local-llm-via-llamacpp))
- **Optional, for PDF output:** LibreOffice (`libreoffice-writer`, providing
  `soffice`). It is installed in the Docker image; locally, without it the API
  still returns `.docx` and logs a warning instead of a PDF — install it with
  `sudo apt install libreoffice-writer`, or set `RENDER_PDF=false` to skip the
  attempt entirely

## Local setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY (and optionally GITHUB_TOKEN)
```

Review UI (optional locally — the API runs without it):

```bash
cd frontend
npm install
npm run build      # writes frontend/dist, which the API serves at "/"
```

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes* | — | LLM calls with the default `anthropic` provider |
| `GITHUB_TOKEN` | no | — | **Fallback** token, used only when a request supplies no `github_token` (see API-REFERENCE.md → `POST /ingest`). Raises GitHub API rate limits **and** unlocks richer contribution data for `github_username` ingestion: with a token the client uses GraphQL `repositoriesContributedTo` (all-time, with description/language/stars) plus per-repo commit counts; without one it falls back to the REST merged-PR search. If the token belongs to **the very username being ingested** it additionally unlocks private org memberships and private repos (see "GitHub coverage" below); for any other username those endpoints are never used |
| `GITHUB_INCLUDE_CONTRIBUTIONS` | no | `true` | Kill switch for the extra search/GraphQL calls that find contributions to repos the user doesn't own. `false` → the source document degrades to owned + organization repos only |
| `GITHUB_MAX_EXTERNAL_REPOS` | no | `15` | How many external (contributed-to) repos to keep, ranked by contribution volume (merged PRs + commits), not recency |
| `GITHUB_INCLUDE_PRIVATE` | no | `true` | Whether private repos are ingested on the self-token path. Their names, descriptions and README excerpts then reach the extraction LLM and are stored under `data/sources/`. `false` → public repos only, while private **org membership** is still discovered |
| `GITHUB_MAX_CONTRIBUTION_PROBES` | no | `150` | Budget for the `GET /repos/{full}/commits?author=` probes that prove a non-owned repo was actually worked on. Repos beyond the budget are dropped, never assumed |
| `GITHUB_MAX_ORG_REPOS` | no | `20` | How many organization/collaborator repos to render, newest contribution first. Each organization keeps at least one repo before recency fills the rest, so an old employer is not evicted by a busy current one |
| `GITHUB_REPOS_PER_EXTRACTION` | no | `10` | How many repos go into one extraction call for a GitHub source. Lower = more calls but less risk of the model truncating (or omitting) its structured output; raise it only if extraction is reliably clean and you want fewer calls |
| `LLM_PROVIDER` | no | `anthropic` | Provider switch (same method as FUND `get_llm`): `anthropic`, `openai`, `google`, `nvidia`, `llamacpp`, `deepseek`, `openrouter`. Non-Anthropic providers need their `langchain-*` package installed and `*_MODEL` vars set to that provider's model ids. Packages for `anthropic`, `openai`, `google`, `nvidia`, and `llamacpp` ship in `requirements.txt`; `deepseek`/`openrouter` need a manual `pip install`. For `deepseek` the factory disables thinking mode per request — DeepSeek thinking models reject the forced tool call that structured output requires. |
| `LLM_API_KEY` | no | falls back to `ANTHROPIC_API_KEY` | Provider API key (*required if `LLM_PROVIDER` isn't `anthropic`) |
| `LLM_TEMPERATURE` | no | unset | Sampling temperature; leave unset for current Claude models (they reject non-default sampling params). For local/OSS providers set ~`0.2` — low temperature keeps the extraction and validation stages factual, which the anti-fabrication gate depends on |
| `LLM_MAX_TOKENS` | no | `8000` | Default output token cap per LLM call |
| `LLM_BASE_URL` | no | — | Provider base URL override (e.g. local llama.cpp) |
| `LLM_STREAM_TIMEOUT_S` | no | `90` | Max seconds of provider-client inactivity before abort. Raise to `300` for local llama.cpp — an 8k-token synthesis output on local hardware can legitimately exceed 90 s |
| `EXTRACTION_MODEL` | no | `claude-haiku-4-5-20251001` | Per-source extraction model |
| `SYNTHESIS_MODEL` | no | `claude-sonnet-5` | Profile synthesis model |
| `TAILORING_MODEL` | no | `claude-sonnet-5` | Job analysis + CV tailoring model |
| `COVER_LETTER_MODEL` | no | `TAILORING_MODEL` | Cover-letter model — same task and same no-fabrication rules as tailoring, so it follows that tier unless set |
| `VALIDATION_MODEL` | no | `claude-sonnet-5` | Anti-fabrication LLM cross-check (override to `claude-opus-4-8` for max precision) |
| `REVIEW_MODEL` | no | `VALIDATION_MODEL` | Model that writes the reviewer's brief when a run pauses for human review — it explains that gate's findings, so it follows that tier unless set |
| `REVIEW_AGENT_ENABLED` | no | `true` | Whether to write that brief at all. `false` → the run still pauses and still lists every flagged item, it just arrives without prose (one fewer LLM call per paused run) |
| `REVIEW_MAX_TOOL_ITERATIONS` | no | `4` | Bound on the review agent's tool-calling loop (it loads skill bodies on demand). Exhausting it yields no brief rather than looping |
| `FRONTEND_DIR` | no | `./frontend/dist` | Built review UI, served at `/`. Absent → `/` redirects to `/docs` and the API is unaffected |
| `DATA_DIR` | no | `./data` | Root of the versioned JSON profile store |
| `SKILLS_DIR` | no | `./skills` | Directory of per-agent `SKILL.md` reasoning skills (FUND skills mechanism). Prompt **content**, not secrets — ships in the image, safe to commit. A missing/empty dir degrades gracefully: each agent falls back to its inline prompt scaffolding and the pipeline still runs |
| `LOG_LEVEL` | no | `INFO` | Root log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`); unknown values fall back to `INFO`. `DEBUG` traces the ingestion pipeline: GitHub repo list + full source document, extraction inputs/results, synthesis payload/profile (noisy libs — httpx/httpcore/urllib3/watchfiles/openai — stay capped at INFO) |
| `LOG_FILE` | no | unset | Log file path (e.g. `./logs/app.log`); unset = console only. Rotates at 10 MB keeping 3 backups; parent dir auto-created; uvicorn access/error logs are routed there too |
| `VALIDATION_SIMILARITY_THRESHOLD` | no | `0.55` | difflib ratio below which a tailored bullet triggers the LLM cross-check |
| `DOCX_TEMPLATE` | no | — | Path to a base `.docx` supplying styles/theme/letterhead for rendered documents. Content is always appended by the renderer, so the template needs no placeholders; one lacking the built-in `Heading 1`/`List Bullet` styles degrades to bold/plain paragraphs. A configured-but-missing path logs a WARNING and falls back to the default template |
| `RENDER_PDF` | no | `true` | Whether to convert each rendered `.docx` to PDF with headless LibreOffice. `false` → `.docx` only, no subprocess call |
| `LIBREOFFICE_BIN` | no | `soffice` | The headless converter binary. Installed in the Docker image (`libreoffice-writer`); if it is missing or fails locally the PDF is skipped with a WARNING and the `.docx` is still returned |
| `LIBREOFFICE_TIMEOUT_S` | no | `120` | Per-conversion timeout; on timeout the PDF is skipped, never the run |
| `EMAIL_BACKEND` | no | `file` | Mail delivery (Phase 7). `file` drops a complete `.eml` in the outbox (see below); `console` logs the code/link; `smtp` sends for real |
| `EMAIL_FROM` | no | `no-reply@localhost` | `From:` address on auth mail |
| `EMAIL_OUTBOX_DIR` | no | `./data/auth/outbox` | Where the `file` backend writes `.eml` files |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_STARTTLS` / `SMTP_TIMEOUT_S` | no | — / `587` / — / — / `true` / `10` | `smtp` backend only; a login is sent only when `SMTP_USER` is set. Credentials via `.env`, never committed |
| `AUTH_VERIFY_METHOD` | no | `code` | `code` (6-digit OTP typed back) or `link` (magic link) |
| `VERIFY_CODE_LENGTH` | no | `6` | Digits in the OTP |
| `AUTH_MAX_CODE_ATTEMPTS` | no | `5` | Wrong-code tries before a challenge is burned |
| `PUBLIC_BASE_URL` | no | `http://localhost:8000` | Base for magic links + the "sign in here" mail — **never** derived from the `Host` header (a forged `Host` would poison the link) |
| `SESSION_COOKIE_NAME` | no | `rb_session` | Session cookie name |
| `SESSION_COOKIE_SECURE` | no | `true` | `Secure` flag on the cookie; set `false` only for local `http://` |
| `SESSION_TTL_S` | no | `1209600` | Session lifetime (14 days), sliding on use |
| `SIGNUP_TTL_S` / `SIGNIN_TTL_S` | no | `1800` / `900` | Sign-up / sign-in challenge lifetimes |
| `AUTH_MAX_SENDS_PER_HOUR` | no | `5` | Challenges emailed per address per hour (mailbomb / code-farming bound) |

> Phase 7.b ships the auth **flow** only; the business routes are not yet behind
> it (that is 7.d). `AUTH_ENABLED` / `SINGLE_USER_EMAIL` arrive with per-user
> roots in 7.c.

**Reading a verification code/link with no mail server.** With the default
`EMAIL_BACKEND=file`, every auth mail is written as a complete `.eml` under
`EMAIL_OUTBOX_DIR` (default `./data/auth/outbox/`), timestamped so the **newest
sorts last**. To complete a sign-up/sign-in locally, open the newest file there
and read the 6-digit code (or click the link):

```bash
ls -t data/auth/outbox/*.eml | head -1 | xargs cat
```

**Sending real mail via Gmail SMTP.** Set `EMAIL_BACKEND=smtp` and point it at
Gmail's submission endpoint. Gmail does **not** accept your normal account
password over SMTP — you must enable 2-Step Verification on the Google account
and generate a 16-character **App Password**
([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)),
then use that as `SMTP_PASSWORD`:

```dotenv
EMAIL_BACKEND=smtp
EMAIL_FROM=you@gmail.com          # Gmail rewrites From to the authenticated user
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587                     # submission port; our mailer upgrades with STARTTLS
SMTP_STARTTLS=true
SMTP_USER=you@gmail.com
SMTP_PASSWORD=abcd efgh ijkl mnop # the App Password (spaces optional), NOT your login password
SMTP_TIMEOUT_S=10
```

Notes:
- Port `587` + STARTTLS is the path this mailer implements; the SSL-on-connect
  port `465` is **not** supported (it needs `SMTP_SSL`, which the mailer does not
  use).
- Keep `SMTP_PASSWORD` in `.env` only — it is a credential; never commit it. If
  the App Password leaks, revoke it from the same Google page and mint a new one.
- Gmail free accounts cap at ~500 recipients/day, which is ample for one code per
  sign-in but not for bulk mail.

Secrets live in `.env` (gitignored) and are loaded via `python-dotenv`; never
commit or hardcode them.

The review UI's **dev server** has its own three variables (`UI_HOST`, `UI_PORT`,
`API_URL`, plus `UI_ALLOWED_HOSTS`) — they are read by `frontend/vite.config.ts`
from the shell, not from `.env`, and are irrelevant in production where the API
serves the built bundle itself. See
[Developing the UI against a running API](#developing-the-ui-against-a-running-api).

## GitHub coverage and rate limits

`github_username` ingestion collects three tiers, each labelled separately in
the source document (see TECHNICAL-DESIGN.md §3):

1. repos owned by the username,
2. repos owned by organizations the user belongs to or collaborates on **and has
   committed to**,
3. repos the user only **contributed** to (merged PRs / commits).

**Where the token comes from.** Each `/ingest` request may carry its own
`github_token` form field; `GITHUB_TOKEN` is only the fallback for requests that
don't. One running server can therefore ingest several people, each with their
own credential, without an `.env` edit or a restart. A request token is used for
that request and nothing else: it is not archived under `data/`, not recorded in
`manifest.json`, and not logged (the `github[%s]: token viewer=%s` DEBUG line
logs the *resolved login*, never the secret). Operators who prefer one shared
credential can simply keep setting `GITHUB_TOKEN` and ignore the field.

**Self-token vs. third-party token.** `GET /users/{u}/orgs` lists only *public*
organization memberships, and GitHub's default for a membership is private — so
a user who belongs to five organizations can look org-less. When the resolved
token belongs to the very username being ingested, the client instead uses the
viewer endpoints `GET /user/orgs` and `GET /user/repos?affiliation=owner,organization_member,collaborator`,
which see private memberships and private repos. Identity is checked with
`GET /user` **per request**, so the same process can grant user B their private
repos and still refuse a token that belongs to neither party: a token for anyone
else falls back to the public endpoints, and a third party's private data can
never be reached. Set `GITHUB_INCLUDE_PRIVATE=false` to keep private repos out of
the source document while still discovering the memberships.

**Access is not contribution.** `affiliation=collaborator` returns every repo the
user was ever invited to — in practice mostly repos they never touched. Each
non-owned repo therefore has to prove a commit via
`GET /repos/{full}/commits?author={u}` before it is rendered; repos already
proven by the merged-PR search or the GraphQL commit counts skip the probe.

**Extraction is batched per repo.** A GitHub source is one document holding
every repo, which at ~50 repos asks the extractor for more structured output
than models reliably return — observed in practice as a response with *no tool
call at all*, losing the entire source while the run still reported success.
Repos are therefore extracted `GITHUB_REPOS_PER_EXTRACTION` at a time, and a
failed batch is retried one repo at a time so the loss is one repo instead of
fifty. Repos that fail even alone are reported (see below) and dropped, and the
run continues.

**Sampling settings matter most here.** Extraction, synthesis and validation all
use forced-tool-call structured output, and sampling that call at a high
temperature makes "no tool call returned" markedly more likely. A run observed
using `LLM_TEMPERATURE=0.9` with `LLM_MAX_TOKENS=16384` for every stage is what
produced the failure above. If you set `LLM_TEMPERATURE` at all, keep it low
(~`0.2`); there is deliberately no per-stage temperature knob.

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

Either way, `/` serves the review UI when `frontend/dist` exists and otherwise
redirects to `/docs`.

Docker (one service, `.env` file, `data/` volume):

```bash
docker compose up --build
# server listens on 0.0.0.0:8000 — API *and* review UI
```

The image is multi-stage: a `node:20-slim` stage runs `npm ci && npm run build`
and the Python runtime copies `dist/` in, so the shipped container has no Node
in it and the UI is always present. `.dockerignore` keeps any host
`node_modules/`/`dist/` out of the build context — the bundle is always built
from `package-lock.json`.

### Developing the UI against a running API

Two separate things are being addressed here and it is worth keeping them
apart: **where the dev server listens** (`UI_HOST`/`UI_PORT`) and **where it
forwards API calls** (`API_URL`).

```bash
cd frontend
npm install                      # once
npm run dev                      # http://localhost:5173, API on localhost:8000
```

Custom IP and port:

```bash
# listen on every interface, port 3000 — reachable at http://<this-machine-ip>:3000
UI_HOST=0.0.0.0 UI_PORT=3000 npm run dev

# ...and talk to an API on another host
UI_HOST=0.0.0.0 UI_PORT=3000 API_URL=http://192.168.0.212:8000 npm run dev

# bind one specific interface only
UI_HOST=192.168.0.212 UI_PORT=3000 npm run dev

# equivalent Vite CLI flags (these win over the env vars)
npm run dev -- --host 0.0.0.0 --port 3000
```

| Variable | Default | Purpose |
|---|---|---|
| `UI_HOST` | `localhost` | Interface the dev/preview server binds. `0.0.0.0` = all interfaces (needed to reach it from another machine); the default stays loopback-only so nothing is exposed unintentionally |
| `UI_PORT` | `5173` | Port it listens on. `strictPort` is set, so a taken port fails loudly instead of silently moving to `5174` — the port you hand out is the port that serves |
| `API_URL` | `http://localhost:8000` | Where the proxy forwards `/ingest`, `/profile`, `/tailor`, `/document`, `/healthz`. Independent of `UI_HOST` — the API can be on another machine |
| `UI_ALLOWED_HOSTS` | — | Comma-separated extra hostnames Vite will answer to. Only needed for a **DNS name** (tunnel, `/etc/hosts` alias); plain IPs work without it |

These four are read by `frontend/vite.config.ts` from the shell environment —
they are **not** part of the backend's `.env`, and they affect `npm run dev` /
`npm run preview` only. A production deployment has no Vite server at all: the
API serves the built bundle on its own host/port (see above).

The dev server proxies the API paths to `API_URL`, so the app talks to
same-origin paths in development exactly as it does in production — there is no
CORS configuration anywhere. Consequently, when serving on `0.0.0.0` for a
browser on another machine, only the **UI** port needs to be reachable from
that browser; `API_URL` is resolved by the Vite process, not by the browser.

`npm run preview` (serving the built `dist/` from `npm run build` rather than
the dev bundle) honours all four variables the same way, proxy included — so a
LAN smoke test of the production bundle is `UI_HOST=0.0.0.0 UI_PORT=3000 npm run
preview`. Note it then defaults to `5173` too, not Vite's usual `4173`.

### Human review is in-process state

A run that pauses for review is checkpointed in a `MemorySaver` inside the API
process. **Restarting the server loses every pending review:**
`POST /tailor/{id}/resume` then returns `409` and the tailoring has to be
re-run. The flagged items themselves are on disk
(`data/documents/{tailor_id}/review.json`), so the record of what was asked
survives — only the ability to continue that run does not. It is also
single-process state: this service must not be scaled to multiple workers or
replicas without swapping in a durable checkpointer, since a resume could land
on a worker that never saw the pause. `uvicorn` defaults to one worker; do not
add `--workers`.

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
cd frontend && npm test      # review UI — vitest + Testing Library, jsdom, fetch mocked
```

Unit tests are the default (`pytest.ini` excludes the `integration` marker).
The frontend suite needs no API running: every call in `src/lib/api.ts` is
mocked per test.

The integration suite also covers PDF conversion against a real LibreOffice
(`tests/integration/test_pdf_render.py`), which needs no API key but does need
a working `soffice` — it skips otherwise. To run it where it is guaranteed to
be present, use the image:

```bash
docker compose build
docker run --rm -v "$PWD/tests:/app/tests" -v "$PWD/pytest.ini:/app/pytest.ini" \
  resume-builder-api sh -c "pip install -q pytest && \
    python -m pytest tests/integration/test_pdf_render.py -m integration"
```

## Smoke test

```bash
curl localhost:8000/healthz
curl -F "cv=@resume.docx" -F "github_username=<user>" localhost:8000/ingest
# with a LinkedIn data export (Settings → "Get a copy of your data")
curl -F "cv=@resume.docx" -F "linkedin_export=@Basic_LinkedInDataExport.zip" \
  localhost:8000/ingest
curl -X POST localhost:8000/tailor -H 'content-type: application/json' \
  -d '{"profile_id": "<id from ingest>", "job_post": "<paste job post>"}'
# ...and with rendered documents + a cover letter
curl -X POST localhost:8000/tailor -H 'content-type: application/json' \
  -d '{"profile_id": "<id>", "job_post": "<paste job post>",
       "render": true, "cover_letter": true}'
TAILOR_ID=<tailor_id from the response>
curl -o cv.docx "localhost:8000/document/$TAILOR_ID"
curl -o cv.pdf  "localhost:8000/document/$TAILOR_ID?format=pdf"
curl -o cover-letter.docx "localhost:8000/document/$TAILOR_ID?kind=cover_letter"
```

If the response carries `"review_required": true`, the validation gate paused
the run for a person (this is the normal path when flags exist and `render` was
requested). Nothing was written; answer it and the same run renders:

```bash
curl -s "localhost:8000/tailor/$TAILOR_ID/review"      # the flagged items + brief
curl -X POST "localhost:8000/tailor/$TAILOR_ID/resume" \
  -H 'content-type: application/json' \
  -d '{"approvals": {"flag-0": true, "flag-1": false}}'
```

Anything not approved is removed from the CV. `409` means no review is pending
— already resumed, or the server restarted (see "Human review is in-process
state"). To skip the pause entirely, send `"approve_flagged": true` with the
original `/tailor` request. A missing `cv.pdf` (404) with a present `cv.docx`
means LibreOffice was unavailable — check the log for `skipping PDF`.

In a browser, all of this is the third panel of the review UI at
`http://<host>:8000/`.

These are one-liners for checking the service is alive. For the full job
description → downloaded CV walkthrough — with the JD in a file, the review
pause and the resume — see
[API-REFERENCE.md § "Worked example — end to end"](API-REFERENCE.md#worked-example--end-to-end).

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
| `data/sources/{run_id}/cv/<original-name>` | Raw uploaded CV bytes, saved **before** parsing. A name already used in the run is suffixed (`CV.docx` → `CV-2.docx` → …) rather than overwritten, and the source id follows the stored name |
| `data/sources/{run_id}/github/github.json` | Serialized GitHub `SourceDocument` — **the repos that reached the profile**, when any were dropped |
| `data/sources/{run_id}/github/github.raw.json` | The GitHub document exactly as fetched, written **only** when repos were dropped. The audit trail of what GitHub really returned must survive the pruning |
| `data/sources/{run_id}/linkedin/linkedin-summary.txt` | The `free_text` input (LinkedIn summary path) |
| `data/sources/{run_id}/linkedin/<original-name>` | Uploaded LinkedIn data export (`.zip` / `.csv`), saved **before** parsing |
| `data/sources/{run_id}/manifest.json` | Index of inputs (category, filename, size, sha256) linked to `profile_id`/`version` |
| `data/output/{run_id}/output.json` | Copy of the synthesized profile |

Each `POST /tailor` execution is likewise tagged with a `tailor_id`:

| Path | Contents |
|---|---|
| `data/documents/{tailor_id}/cv.docx` / `cv.pdf` | The rendered CV (PDF only when LibreOffice ran) |
| `data/documents/{tailor_id}/cover-letter.docx` / `.pdf` | The rendered cover letter, when one was requested |
| `data/documents/{tailor_id}/tailor.json` | The run's tailored CV, validation result and cover letter — always written, even when nothing was rendered |

- **Retention / privacy:** raw CVs are now **retained** on disk (previously they
  were parsed from a temp file and immediately deleted). Treat `data/sources/`
  as sensitive PII — it holds original résumés and pasted personal summaries.
  `data/` is gitignored; back it up and purge it per your retention policy.
  There is no API for deleting a run yet — remove `data/sources/{run_id}`,
  `data/output/{run_id}` and `data/documents/{tailor_id}` directories manually.
  Rendered documents are PII too: a `.docx`/`.pdf` under `data/documents/` is a
  finished résumé complete with contact details, and it is served by
  `GET /document/{tailor_id}` to anyone who can reach the API and knows the id.
- **Log correlation:** with `LOG_FILE` set, every pipeline log line is tagged
  `[run:<run_id>]`, so a single run is greppable across steps:
  `grep '\[run:<run_id>\]' logs/app.log`.
- **Disk growth:** sources + output accumulate per run and documents per
  tailoring run; prune old `run_id` / `tailor_id` directories periodically.
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
- **UI transport errors (Phase 6.c) — no setup change, no `vite.config.ts`
  change.** When a user reports a UI error, the wording now tells you where to
  look: `Could not reach the API (<path>) — is the server running?` means the
  request never got a response (container down, proxy hang-up, VPN, offline) —
  check the API is up and reachable from the browser's host, and expect *no*
  corresponding line in the API log. Any other message is the API's own
  `detail` from an HTTP error, so the log will have it. `Could not refresh …`
  is non-fatal: the profile is still on screen and retryable. One expected
  side effect: the UI cancels superseded profile requests, so a dropped
  connection in the log is not necessarily a failure.
- **Session clearing (Phases 6.a/6.b) — no setup change.** No env vars, no
  dependencies and no `vite.config.ts` change: "Clear everything" and the
  clear-on-new-profile behaviour are browser-side state only. Worth knowing
  when a user says they "cleared" their data — nothing under `data/` is
  touched, so profiles, runs and documents are all still there and reloadable
  by id; deleting them is still the manual prune described above.
- **Skipped repos are reported, not just logged (Phase 5.c).** Anything the
  extractor could not read comes back in `source_errors` on the `/ingest`
  response and as `warning` events on the SSE stream, and the UI lists it. A
  partial run is no longer indistinguishable from a clean one. When a response
  with no tool call is what failed, the log now carries the provider's
  `finish_reason`, token usage and a content preview instead of the bare `: None`
  it used to print — check those first, since a `length` finish reason means the
  batch was too big and `GITHUB_REPOS_PER_EXTRACTION` should come down.

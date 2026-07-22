# API Reference

Base URL: `http://localhost:8000` (single FastAPI service). All responses are JSON
unless noted. Schemas referenced below are defined in `src/models/schemas.py`.

## GET /

Redirects (307) to `/docs` — FastAPI's interactive Swagger UI, the
browser-friendly way to explore and try every endpoint below.

## GET /healthz

Liveness probe. Returns `{"status": "ok"}`.

## POST /ingest

Runs the ingestion graph over the provided sources and stores the resulting
profile. By default a fresh `profile_id` is minted and stored as v1; pass
`profile_id` to direct the result into a specific profile instead. Each call is
tagged with a `run_id` that archives the raw inputs under
`data/sources/{run_id}/` and a copy of the output under
`data/output/{run_id}/output.json` (see OPERATIONS.md → Run tracking & retention).

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `cv` | file(s) | no* | One or more `.docx` / `.pdf` CVs |
| `linkedin_export` | file(s) | no* | One or more official LinkedIn data exports — the `.zip` from Settings → "Get a copy of your data", or a single `.csv` from it (e.g. `Positions.csv`). Other extensions → 400; an archive with no recognizable section → 400 |
| `github_username` | text | no* | Public GitHub profile to ingest — owned repos, org/collaborator repos, and contributions to external repos (public data only) |
| `free_text` | text | no* | Pasted bio/notes passthrough (also the LinkedIn-summary path) |
| `job_id` | text | no | Client-generated id for SSE progress; subscribe to `GET /ingest/{job_id}/events` before POSTing. Server generates one if omitted. Doubles as the `run_id`. |
| `profile_id` | text | no | Target profile for the result. **Existing id** → a new version is appended; **new id** → created at v1. Must be 1–64 chars of `[A-Za-z0-9_-]` (else 400). Omitted → the server mints a fresh id. Distinct from `run_id` (which is one execution). |

*At least one of `cv`, `linkedin_export`, `github_username`, `free_text` is
required (else 400). Unsupported CV or LinkedIn-export extensions → 400; an
unreadable LinkedIn export (corrupt ZIP, or no recognized section) → 400. An
invalid `profile_id` → 400. Graph failure → 500.

**Response 200:**

```json
{
  "job_id": "…",
  "run_id": "…",
  "profile_id": "…",
  "version": 1,
  "profile": { CareerProfile — includes "conflicts": [Conflict, …] }
}
```

`run_id` equals `job_id`. Its archive lives at `data/sources/{run_id}/`
(raw CV under `cv/`, `github/github.json`, `linkedin/` holding the uploaded
export archive and/or `linkedin-summary.txt`, plus `manifest.json`) and
`data/output/{run_id}/output.json`.

**Example — CV + LinkedIn export in one call:**

```bash
curl -F "cv=@resume.docx" \
     -F "linkedin_export=@Basic_LinkedInDataExport.zip" \
     localhost:8000/ingest
```

## GET /ingest/{job_id}/events

Server-Sent Events stream of per-node ingestion progress.

Events: `node` (data = node name: `ingest_sources`, `extract_source`,
`synthesize_profile`, `store_profile`), `error` (data = message), `done`
(terminal). The queue is discarded after `done`.

## GET /profile/{profile_id}

Latest version by default; `?version=n` for a specific version. 404 if unknown.

**Response 200:** `{"profile_id", "version", "versions": [1, 2, …], "profile": CareerProfile}`

## PUT /profile/{profile_id}

Save a user-edited profile as a new version (Phase 1's conflict-resolution
mechanism). Body: full `CareerProfile` JSON. 404 if the profile doesn't exist.

**Response 200:** `{"profile_id", "version": <new version>}`

## POST /tailor

Generates a CV targeted at one job description, from a stored profile. Runs the
tailoring graph
`analyze_job → tailor_cv → validate_cv → [write_cover_letter] → render_document`
(`src/agents/tailoring_graph.py`) synchronously — there is no SSE variant, the
call returns when rendering is done. Ingestion is not re-run: the profile is
loaded from the store, so the same profile can be re-tailored to any number of
job posts cheaply.

**Request:** JSON

| Field | Type | Required | Meaning |
|---|---|---|---|
| `profile_id` | string | yes | Profile to tailor from, as returned by `POST /ingest` |
| `job_post` | string | yes | Raw pasted job-post text; parsed by the job-analysis agent, no pre-structuring needed |
| `version` | int \| null | no | Profile version to use; defaults to the latest |
| `render` | bool | no (`false`) | Also render document files (`.docx`, plus `.pdf` when LibreOffice is available), downloadable from `GET /document/{tailor_id}` |
| `cover_letter` | bool | no (`false`) | Also generate a cover letter. Returned as JSON either way; rendered too when `render` is set. Costs one extra LLM call |
| `approve_flagged` | bool | no (`false`) | Render even though validation raised flags. Only meaningful with `render` — see "The render gate" below |

```json
{"profile_id": "5f3c…", "job_post": "<pasted job post text>", "version": 2,
 "render": true, "cover_letter": true}
```

**Errors:** `404` unknown `profile_id` (or unknown `version`); `400` `job_post`
empty or whitespace-only; `422` malformed body (missing field / wrong type).

**Response 200:**

```json
{
  "profile_id": "5f3c…",
  "tailor_id": "a1b2c3d4e5f6",
  "job_requirements": {
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "required_skills": ["Python", "PostgreSQL"],
    "preferred_skills": ["LangGraph"],
    "responsibilities": ["Own the ingestion pipeline"],
    "seniority": "senior",
    "keywords_for_ats": ["microservices", "async"]
  },
  "tailored_cv": {
    "headline": "Senior Backend Engineer — Python & data pipelines",
    "summary": "2–4 sentences, framed for this job post",
    "selected_experiences": [
      {
        "company": "…", "title": "…",
        "start_date": "2021-03", "end_date": "2024-01", "location": "…",
        "bullets": ["…"],
        "source": "cv_docx:resume.docx"
      }
    ],
    "selected_projects": [
      {"name": "…", "description": "…", "technologies": ["…"],
       "role": "…", "url": "…", "source": "github:thomas-choi/repo"}
    ],
    "highlighted_skills": ["Python", "FastAPI"],
    "relevance_notes": {"<item>": "why it was selected"}
  },
  "validation": {
    "passed": true,
    "needs_review": false,
    "flags": [
      {"item": "<flagged bullet or skill text>",
       "kind": "bullet | skill | experience | project",
       "reason": "…", "similarity": 0.41}
    ]
  },
  "cover_letter": {
    "greeting": "Dear Hiring Manager,",
    "body_paragraphs": ["…", "…", "…"],
    "closing": "Sincerely,"
  },
  "documents": [
    {"kind": "cv", "format": "docx", "filename": "cv.docx",
     "size_bytes": 37421, "url": "/document/a1b2c3d4e5f6?kind=cv&format=docx"}
  ],
  "render_skipped": null
}
```

Notes on the output:

- `selected_experiences` / `selected_projects` are a **subset** of the profile's
  entries, re-ordered and re-worded for relevance — never new entries. Each
  keeps its `source` id for traceability.
- `relevance_notes` is internal reasoning (why each item was picked). It is not
  meant to be rendered into the CV, and the document renderer omits it.
- `validation.needs_review = true` means at least one claim could not be traced
  back to `CareerProfile.raw_source_map` — review `flags` before using the CV.
  Nothing is silently dropped or auto-approved; through Phase 3 the review is
  the caller's responsibility (Phase 4 moves it server-side).
- `tailor_id` identifies this run; it is the key for `GET /document/{tailor_id}`
  and the directory name under `data/documents/`.
- `cover_letter` is `null` unless `cover_letter: true` was requested.
- `documents` lists what was actually written (empty when `render` was not set
  or the gate blocked it); each entry carries a ready-made `url`. A `pdf` entry
  is absent when LibreOffice was unavailable — the `docx` is always there.
- `render_skipped` is `null` on a successful render, otherwise a human-readable
  reason (`"rendering not requested"`, or the flag count needing review).

**The render gate.** Rendering is refused while `validation.needs_review` is
true: a claim the validator could not trace must not silently become a polished
file. The response still contains the full `tailored_cv` and `flags`, so review
them, then re-run the identical request with `"approve_flagged": true` to
render anyway. (Re-running costs the LLM calls again; Phase 4 replaces this
with a resumable server-side review.)

### Worked example — end to end

**Step 1 — get a `profile_id`.** Tailoring reads a stored profile, so you need
one ingest first. Keep the id; every later job post reuses it.

```bash
PROFILE_ID=$(curl -s -X POST localhost:8000/ingest \
  -F cv=@resume.docx \
  -F github_username=thomas-choi \
  | jq -r '.profile_id')

echo "$PROFILE_ID"        # e.g. 9f2c1e04-… — reuse this for every job post
```

**Step 2 — put the job post in a file.** Job posts are multi-line text with
quotes, `$`, and bullet characters; pasting that straight into a shell string
breaks the JSON. Save it verbatim instead:

```bash
cat > job.txt <<'EOF'
Senior Backend Engineer — Acme
We're looking for someone to own our ingestion pipeline.
Requirements: 5+ years Python, PostgreSQL, async APIs.
Nice to have: LangGraph, Docker, AWS.
EOF
```

The `<<'EOF'` quoting matters — it stops the shell expanding `$` or backticks
inside the posting.

**Step 3 — tailor.**

```bash
curl -s -X POST localhost:8000/tailor \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg pid "$PROFILE_ID" --rawfile job job.txt \
          '{profile_id: $pid, job_post: $job}')" \
  -o tailored.json

jq '{headline: .tailored_cv.headline,
     experiences: [.tailored_cv.selected_experiences[] | .company + " — " + .title],
     skills: .tailored_cv.highlighted_skills,
     needs_review: .validation.needs_review,
     flags: [.validation.flags[] | {kind, item, reason}]}' tailored.json
```

What each piece does:

| Piece | Why it's there |
|---|---|
| `-s` | silent — suppresses curl's progress meter so only the JSON body is captured |
| `-X POST` | the endpoint is POST-only; a GET returns 405 |
| `-H 'Content-Type: application/json'` | **required.** The body is a Pydantic model, not a form. Omit this and FastAPI rejects the request with 422 |
| `jq -n --arg pid … --rawfile job job.txt '{…}'` | builds the request body. `--rawfile` reads `job.txt` as one string and **JSON-escapes the newlines and quotes for you** — this is the part that makes a real job post work |
| `-o tailored.json` | writes the response to disk. Worth doing: the response holds the full CV, and re-running costs another LLM call |
| trailing `jq '{…}'` | pulls out the parts you actually read — headline, chosen roles, skills, and any validation flags |

**Step 4 — check the flags before using the CV.** Anything in
`validation.flags` is a claim the validator could not trace back to your
profile. Gate on it:

```bash
jq -e '.validation.needs_review | not' tailored.json >/dev/null \
  && echo "clean — safe to use" \
  || jq -r '.validation.flags[] | "[\(.kind)] \(.item)\n    → \(.reason) (similarity \(.similarity // "n/a"))"' tailored.json
```

**Variants**

Pin a specific profile version instead of the latest (e.g. to reproduce an
earlier run after editing the profile):

```bash
jq -n --arg pid "$PROFILE_ID" --rawfile job job.txt \
   '{profile_id: $pid, job_post: $job, version: 2}'
```

Tailor the same profile to several postings — ingestion is not repeated, so
each is just one tailoring run:

```bash
for job in jobs/*.txt; do
  curl -s -X POST localhost:8000/tailor \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg pid "$PROFILE_ID" --rawfile j "$job" \
            '{profile_id: $pid, job_post: $j}')" \
    -o "out/$(basename "$job" .txt).json"
done
```

Short one-liner for a quick smoke test (fine only because the text has no
newlines or quotes — use the `--rawfile` form for anything real):

```bash
curl -s -X POST localhost:8000/tailor \
  -H 'Content-Type: application/json' \
  -d '{"profile_id":"'"$PROFILE_ID"'","job_post":"Senior Python engineer, FastAPI and PostgreSQL"}' \
  | jq '.tailored_cv.headline'
```

Ask for documents and a cover letter, then download them:

```bash
TAILOR_ID=$(curl -s -X POST localhost:8000/tailor \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg pid "$PROFILE_ID" --rawfile job job.txt \
          '{profile_id: $pid, job_post: $job, render: true, cover_letter: true}')" \
  -o tailored.json -w '' ; jq -r '.tailor_id' tailored.json)

jq -r '.render_skipped // "rendered"' tailored.json
jq -r '.documents[] | "\(.kind)/\(.format)\t\(.url)"' tailored.json

curl -s -o cv.docx          "localhost:8000/document/$TAILOR_ID"
curl -s -o cv.pdf           "localhost:8000/document/$TAILOR_ID?format=pdf"
curl -s -o cover-letter.pdf "localhost:8000/document/$TAILOR_ID?kind=cover_letter&format=pdf"
```

If `render_skipped` mentions review, inspect the flags as in Step 4 and re-run
the same command with `render: true, approve_flagged: true`.

If you'd rather not build JSON by hand at all, `GET /` redirects to `/docs`
(Swagger UI), where `POST /tailor` has a form with the same fields.

**Not yet available (see Planned below):** approval of flagged items is a
client-side re-run (`approve_flagged`), not a resumable server-side review —
that arrives in Phase 4.

> **Phase 1.e (2026-07-21) — no API change.** Null-tolerant extraction schemas
> and item-level salvage are internal: no new fields, parameters, or status
> codes. `POST /ingest` simply stops returning 500 when a source contains an
> item the extractor legitimately left empty (e.g. a GitHub repo with no
> description).

> **Phase 1.f (2026-07-21) — no API change.** Broader GitHub coverage (org
> repos + contributions to repos the user doesn't own) is internal to the
> ingestion tool: `POST /ingest` takes the same `github_username` and returns
> the same `CareerProfile`, with more of it populated. Coverage is tuned by the
> `GITHUB_INCLUDE_CONTRIBUTIONS` / `GITHUB_MAX_EXTERNAL_REPOS` env vars, not by
> request fields.

> **Phase 1.g (2026-07-21) — no API change.** Private org membership discovery
> (self-token) and the commit probe that keeps organization repos honest are
> internal to the ingestion tool: `POST /ingest` takes the same
> `github_username` and returns the same `CareerProfile`. Whether private repos
> are read is an operator setting (`GITHUB_INCLUDE_PRIVATE`), never a request
> field — the endpoint still accepts no caller-supplied credential, so it can
> only ever reach private data belonging to the configured token's own account.

> **Phase 2 (2026-07-21) — one new request field.** `POST /ingest` accepts
> `linkedin_export` file uploads (see the request table above). No other
> endpoint changes: the LinkedIn export becomes an ordinary `SourceDocument`
> inside the same ingestion graph, so the response shape, the SSE events, and
> `/tailor` are untouched.

> **Phase 3 (2026-07-21) — three new request fields, four new response fields,
> one new endpoint.** `POST /tailor` gains `render`, `cover_letter` and
> `approve_flagged`, and returns `tailor_id`, `cover_letter`, `documents` and
> `render_skipped`; rendered files are downloaded from
> `GET /document/{tailor_id}`. All new request fields default to `false`, so a
> Phase 1/2 caller's request behaves exactly as before — the only response
> change it sees is the four added keys.

## GET /document/{tailor_id}

Downloads a document rendered by `POST /tailor`.

**Path:** `tailor_id` — from the tailor response. Restricted to
`[A-Za-z0-9_-]{1,64}`; it is a directory name in the document store.

**Query:**

| Param | Values | Default | Meaning |
|---|---|---|---|
| `kind` | `cv`, `cover_letter` | `cv` | Which document |
| `format` | `docx`, `pdf` | `docx` | Which format |

**Response 200:** the file, as
`application/vnd.openxmlformats-officedocument.wordprocessingml.document` or
`application/pdf`, with a `Content-Disposition` filename of `cv.docx`,
`cv.pdf`, `cover-letter.docx` or `cover-letter.pdf`.

**Errors:**

- `400` — unknown `kind`/`format`, or a `tailor_id` that is not a safe name.
- `404` — that document was not rendered: `render` was not set, the validation
  gate skipped it (`render_skipped` in the tailor response says which), no
  cover letter was requested, or — for `format=pdf` — LibreOffice was
  unavailable and only the `.docx` exists.

```bash
curl -s -o cv.docx "localhost:8000/document/$TAILOR_ID"
curl -s -o cover-letter.pdf "localhost:8000/document/$TAILOR_ID?kind=cover_letter&format=pdf"
```

Documents persist under `data/documents/{tailor_id}/` (alongside a `tailor.json`
copy of the run's CV, validation result and cover letter), so the same URL keeps
working after a restart. There is no listing or deletion endpoint yet — the
`documents` array in the tailor response is the index.

## Planned (later phases)

- Phase 4: `GET /tailor/{id}/review`, `POST /tailor/{id}/resume` (server-side human-in-the-loop).

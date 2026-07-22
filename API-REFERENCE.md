# API Reference

Base URL: `http://localhost:8000` (single FastAPI service). All responses are JSON
unless noted. Schemas referenced below are defined in `src/models/schemas.py`.

## GET /

Serves the review UI (Phase 4) when a built frontend is present at
`FRONTEND_DIR` (default `./frontend/dist`, and always the case in the Docker
image). Its static assets are served from the same origin, so the browser calls
the endpoints below directly.

Without a build — a backend-only checkout, or `uvicorn` before `npm run build`
— `/` redirects (307) to `/docs`, FastAPI's interactive Swagger UI. Every API
route keeps its path in both modes.

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
| `github_username` | text | no* | GitHub profile to ingest — owned repos, org/collaborator repos, and contributions to external repos |
| `github_token` | text | no | GitHub token **for this request only**, overriding the server's `GITHUB_TOKEN`. A token belonging to `github_username` also unlocks their private repos and private org memberships; a token for anyone else only raises rate limits and never reaches a viewer endpoint. Blank/omitted → the configured `GITHUB_TOKEN` is used. Never archived, never written to `manifest.json`, never logged |
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
  "source_errors": [
    {"source": "github:alice", "repo": "alice/repo-4", "reason": "no tool call returned"}
  ],
  "profile": { CareerProfile — includes "conflicts": [Conflict, …] }
}
```

`source_errors` lists everything the extractor could not read; it is `[]` on a
clean run. `repo` names a single GitHub repository, or is `null` when a whole
source failed. A partial run still returns **200** — the profile is built from
what survived — so this field is the only thing distinguishing it from a
complete one. The same items stream as `warning` SSE events while the run is in
flight.

`run_id` equals `job_id`. Its archive lives at `data/sources/{run_id}/`
(raw CV under `cv/`, `github/github.json`, `linkedin/` holding the uploaded
export archive and/or `linkedin-summary.txt`, plus `manifest.json`) and
`data/output/{run_id}/output.json`.

Two uploads sharing a filename are both kept: the second is archived as
`CV-2.docx` (then `CV-3.docx`, …) and its source id follows the **stored** name,
so the two stay distinct in `manifest.json` and in `CareerProfile.raw_source_map`.

**Example — CV + LinkedIn export in one call:**

```bash
curl -F "cv=@resume.docx" \
     -F "linkedin_export=@Basic_LinkedInDataExport.zip" \
     localhost:8000/ingest
```

**Example — two CVs plus GitHub with a per-request token:**

```bash
curl -F "cv=@CV.docx" -F "cv=@CV.pdf" \
     -F "github_username=alice" -F "github_token=$ALICE_GITHUB_TOKEN" \
     localhost:8000/ingest
```

## GET /ingest/{job_id}/events

Server-Sent Events stream of per-node ingestion progress.

Events: `node` (data = node name: `ingest_sources`, `extract_source`,
`synthesize_profile`, `store_profile`), `warning` (data = `"<repo or source>:
<reason>"`, one per skipped item), `error` (data = message), `done` (terminal).
The queue is discarded after `done`. `warning` is advisory — every item it
reports also comes back in `source_errors` on the `POST /ingest` response, so a
client that misses the stream loses nothing.

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
`analyze_job → tailor_cv → validate_cv → prepare_review → human_review → [write_cover_letter] → render_document`
(`src/agents/tailoring_graph.py`) synchronously — there is no SSE variant, the
call returns when rendering is done, **or when the run pauses for human
review**. Ingestion is not re-run: the profile is loaded from the store, so the
same profile can be re-tailored to any number of job posts cheaply.

For what each of those nodes reads and writes — and the three places a run can
stop — see [TECHNICAL-DESIGN.md § "From job description to targeted
CV"](TECHNICAL-DESIGN.md#from-job-description-to-targeted-cv-one-request-step-by-step).
The worked `curl` example below covers the same path from the caller's side.

**Request:** JSON

| Field | Type | Required | Meaning |
|---|---|---|---|
| `profile_id` | string | yes | Profile to tailor from, as returned by `POST /ingest` |
| `job_post` | string | yes | Raw pasted job-post text; parsed by the job-analysis agent, no pre-structuring needed |
| `version` | int \| null | no | Profile version to use; defaults to the latest |
| `render` | bool | no (`false`) | Also render document files (`.docx`, plus `.pdf` when LibreOffice is available), downloadable from `GET /document/{tailor_id}` |
| `cover_letter` | bool | no (`false`) | Also generate a cover letter. Returned as JSON either way; rendered too when `render` is set. Costs one extra LLM call |
| `approve_flagged` | bool | no (`false`) | Accept every validation flag up front: the run does **not** pause for review and renders regardless. Only meaningful with `render` — see "The render gate" below |

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
  "render_skipped": null,
  "review_required": false,
  "review": null,
  "review_url": null
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
  Nothing is silently dropped or auto-approved. When `render` was requested the
  run pauses on those flags rather than returning them for the client to
  honour (see "The render gate").
- `review_required`, `review`, `review_url` (Phase 4) describe that pause:
  `review` is the `ReviewRequest` the graph is waiting on, and the run stays
  paused until `POST /tailor/{tailor_id}/resume`. All three are `null`/`false`
  on a run that did not pause.
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
file. What happens next depends on the request:

| Request | Behaviour |
|---|---|
| `render: true`, flags raised | The graph **pauses** at `human_review`: `review_required: true`, `review` holds the flagged items, `documents` is empty. Answer with `POST /tailor/{tailor_id}/resume` — the same run then renders. |
| `render: true`, `approve_flagged: true` | No pause; every flag is accepted and the CV renders as-is. |
| `render: false` | Nothing can be rendered, so nothing is gated: flags come back in `validation` for inspection and no review is requested. |

Pausing (rather than returning and asking the caller to re-run) matters twice
over: the run is not charged for a second round of LLM calls, and the CV that
renders is byte-for-byte the one that was reviewed — a re-run would produce a
different draft than the one a person approved.

## GET /tailor/{tailor_id}/review

The flagged items a paused run is waiting on (Phase 4).

**Path:** `tailor_id` — from the tailor response. Restricted to
`[A-Za-z0-9_-]{1,64}`.

**Response 200:**

```json
{
  "pending": true,
  "tailor_id": "a1b2c3d4e5f6",
  "brief": "Plain-prose explanation of each flag, written by the review agent",
  "items": [
    {"id": "flag-0",
     "item": "Ran a team of 40 engineers",
     "kind": "bullet | skill | experience | project",
     "reason": "No profile bullet mentions managing a team",
     "similarity": 0.21,
     "closest_profile_text": "Led migration of the data pipeline to PostgreSQL",
     "source": "cv_docx:resume.docx"}
  ]
}
```

- `id` is what a decision refers to; it is stable for the life of the run.
- `closest_profile_text` / `source` are the nearest sourced claim the gate
  could find, so the reviewer can judge without re-reading the whole profile.
  Both are `null` when nothing was close.
- `brief` is written by the tool-calling review agent and is `""` when the
  agent is disabled (`REVIEW_AGENT_ENABLED=false`) or its call failed — the
  items are what gate rendering, never the prose.
- `pending: false` means the review was already answered (or its checkpoint was
  lost to a restart): the record is served from
  `data/documents/{tailor_id}/review.json`, but `resume` will refuse it.

**Errors:** `400` unsafe `tailor_id`; `404` that run never paused for review.

## POST /tailor/{tailor_id}/resume

Answers a pending review; the same run continues to the cover letter and
rendering.

**Request:** JSON

| Field | Type | Required | Meaning |
|---|---|---|---|
| `approvals` | object | no (`{}`) | `ReviewItem.id` → keep it (`true`) or remove it (`false`) |
| `approve_all` | bool | no (`false`) | Keep every flagged item |
| `notes` | string | no (`""`) | Free text recorded with the decision |

```bash
curl -s -X POST "localhost:8000/tailor/$TAILOR_ID/resume" \
     -H 'Content-Type: application/json' \
     -d '{"approvals": {"flag-0": true, "flag-1": false},
          "notes": "Kubernetes was never used in production."}'
```

**Anything not approved is removed from the CV** — not just from
`highlighted_skills` / `bullets` / `selected_experiences` / `selected_projects`,
but also from the `summary` and `headline` prose that may restate it (whole
sentences naming a rejected claim are dropped; a headline that names one falls
back to the profile's own headline). An item left out of `approvals` counts as
**not** approved: silence is not consent for a claim the gate could not trace.

**Response 200:** the same shape as `POST /tailor`, now with
`review_required: false`, the pruned `tailored_cv`, `documents`, and a
`validation` whose `needs_review` is `false` and whose `flags` list only the
items the person chose to keep (kept for provenance — a human accepting a claim
is not the gate having traced it).

**Errors:** `400` unsafe `tailor_id`; `409` no review pending — it was already
resumed, never paused, or the server restarted (the checkpointer is
in-process; see OPERATIONS.md).

### Worked example — end to end

The whole path in `curl`, from a job description on your clipboard to a `.docx`
on disk. Each shell step below maps to the pipeline steps in
[TECHNICAL-DESIGN.md § "From job description to targeted
CV"](TECHNICAL-DESIGN.md#from-job-description-to-targeted-cv-one-request-step-by-step):

| Shell step | Pipeline steps it triggers |
|---|---|
| 1 — ingest | the *ingestion* graph (once; not part of tailoring) |
| 2 — save the JD | none — local file handling |
| 3 — tailor | 0 accept → 1 `analyze_job` → 2 `tailor_cv` → 3 `validate_cv` → 4 `prepare_review`/`human_review` (pauses here if flagged) → 5 `write_cover_letter` → 6 `render_document` → 7 respond |
| 4 — read the result | none — reads the response you already have |
| 5 — answer the review, if it paused | resumes the *same* run from 4 onward |
| 6 — download | 8 `GET /document` |

**Step 1 — get a `profile_id`.** Tailoring reads a stored profile, so you need
one ingest first. Keep the id; every later job post reuses it.

```bash
PROFILE_ID=$(curl -s -X POST localhost:8000/ingest \
  -F cv=@resume.docx \
  -F github_username=thomas-choi \
  | jq -r '.profile_id')

echo "$PROFILE_ID"        # e.g. 9f2c1e04-… — reuse this for every job post
```

**Step 2 — save the job description to a file.** Job posts are multi-line text with
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

**Step 3 — send the JD and ask for the documents.** This one call is the entire
tailoring pipeline: it reads the posting, writes the CV, validates every claim,
writes the cover letter, and renders the files.

```bash
curl -s -X POST localhost:8000/tailor \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg pid "$PROFILE_ID" --rawfile job job.txt \
          '{profile_id: $pid, job_post: $job, render: true, cover_letter: true}')" \
  -o tailored.json

TAILOR_ID=$(jq -r '.tailor_id' tailored.json)
echo "$TAILOR_ID"         # e.g. 1994c6fe7642 — the id you download documents by
```

What each piece does:

| Piece | Why it's there |
|---|---|
| `-s` | silent — suppresses curl's progress meter so only the JSON body is captured |
| `-X POST` | the endpoint is POST-only; a GET returns 405 |
| `-H 'Content-Type: application/json'` | **required.** The body is a Pydantic model, not a form. Omit this and FastAPI rejects the request with 422 |
| `jq -n --arg pid … --rawfile job job.txt '{…}'` | builds the request body. `--rawfile` reads `job.txt` as one string and **JSON-escapes the newlines and quotes for you** — this is the part that makes a real job post work |
| `render: true` | also write `.docx`/`.pdf` files. Omit for the JSON-only path |
| `cover_letter: true` | also write a cover letter (one extra LLM call). Omit to skip it |
| `-o tailored.json` | writes the response to disk. Worth doing: the response holds the full CV, and re-running costs the LLM calls again |

The call is synchronous and takes seconds to a minute — it is doing two LLM
calls plus one per claim that needs the strict check, plus one for the letter.

**Step 4 — read what came back.** The parts you actually act on: the CV, whether
anything was flagged, and what got rendered.

```bash
jq '{headline: .tailored_cv.headline,
     experiences: [.tailored_cv.selected_experiences[] | .company + " — " + .title],
     skills: .tailored_cv.highlighted_skills,
     needs_review: .validation.needs_review,
     rendered: [.documents[] | "\(.kind).\(.format)"],
     render_skipped}' tailored.json
```

Anything in `validation.flags` is a claim the validator could not trace back to
your profile. Gate on it before using the CV:

```bash
jq -e '.validation.needs_review | not' tailored.json >/dev/null \
  && echo "clean — safe to use" \
  || jq -r '.validation.flags[] | "[\(.kind)] \(.item)\n    → \(.reason) (similarity \(.similarity // "n/a"))"' tailored.json
```

**Step 5 — answer the review, only if it paused.** When flags exist the run
stops before rendering: `review_required` is `true`, `documents` is empty, and
`review.items` holds what a person has to decide on. Read them (step 4 already
has them; `GET /tailor/$TAILOR_ID/review` re-fetches), then resume the *same*
run — same `tailor_id`, no further LLM calls for the CV:

```bash
jq -e '.review_required' tailored.json >/dev/null && {
  jq -r '.review.brief, (.review.items[] | "\(.id)  [\(.kind)] \(.item)")' tailored.json

  # Keep the first flagged item, drop everything else. An id you leave out is
  # removed from the CV — silence is not approval.
  curl -s -X POST "localhost:8000/tailor/$TAILOR_ID/resume" \
    -H 'Content-Type: application/json' \
    -d '{"approvals": {"flag-0": true}}' \
    -o tailored.json
}
```

To skip the pause entirely — you have already decided to accept whatever the
gate finds — send `"approve_flagged": true` with the original request in step 3.

**Step 6 — download the documents.**

```bash
jq -r '.documents[] | "\(.kind)/\(.format)\t\(.url)"' tailored.json

curl -s -o cv.docx          "localhost:8000/document/$TAILOR_ID"
curl -s -o cv.pdf           "localhost:8000/document/$TAILOR_ID?format=pdf"
curl -s -o cover-letter.docx "localhost:8000/document/$TAILOR_ID?kind=cover_letter"
curl -s -o cover-letter.pdf  "localhost:8000/document/$TAILOR_ID?kind=cover_letter&format=pdf"
```

A `404` here means that document was not rendered — check `render_skipped` in
the response, and for `?format=pdf` specifically, that LibreOffice is installed
server-side (see OPERATIONS.md). The `.docx` is always written when a render
happened; the files stay downloadable by `tailor_id` afterwards.

**All six steps in one block**

```bash
BASE=localhost:8000

PROFILE_ID=$(curl -s -X POST $BASE/ingest \
  -F cv=@resume.docx -F github_username=your-gh-user | jq -r '.profile_id')

cat > job.txt <<'EOF'
Senior Backend Engineer — Acme
We're looking for someone to own our ingestion pipeline.
Requirements: 5+ years Python, PostgreSQL, async APIs.
EOF

curl -s -X POST $BASE/tailor -H 'Content-Type: application/json' \
  -d "$(jq -n --arg pid "$PROFILE_ID" --rawfile job job.txt \
          '{profile_id: $pid, job_post: $job, render: true, cover_letter: true}')" \
  -o tailored.json

jq -r '.validation.flags[]? | "FLAG [\(.kind)] \(.item) → \(.reason)"' tailored.json
jq -r '.render_skipped // "rendered"' tailored.json

TAILOR_ID=$(jq -r '.tailor_id' tailored.json)
curl -s -o cv.docx "$BASE/document/$TAILOR_ID"
curl -s -o cv.pdf  "$BASE/document/$TAILOR_ID?format=pdf"
```

**Variants**

JSON only — no files, no cover letter, no LibreOffice involved. This is the
Phase 1/2 path and stays the default; steps 1–4 above still apply, `documents`
comes back empty with `render_skipped: "rendering not requested"`:

```bash
curl -s -X POST localhost:8000/tailor \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg pid "$PROFILE_ID" --rawfile job job.txt \
          '{profile_id: $pid, job_post: $job}')" \
  -o tailored.json
```

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

> **Phase 5.c (2026-07-22) — one new response field, one new SSE event.**
> `POST /ingest` returns `source_errors` (always present, `[]` when nothing was
> skipped) and streams a `warning` event per skipped item. No request field
> changed and no status code changed: a partial extraction was already a 200,
> it just had no way to say so. Batch size is an operator setting
> (`GITHUB_REPOS_PER_EXTRACTION`), not a request field.

> **Phase 5.a/5.b (2026-07-21) — one new request field.** `POST /ingest` accepts
> `github_token`, which overrides the server's `GITHUB_TOKEN` for that request
> only; omitting it leaves behavior exactly as before. This supersedes the
> Phase 1.g note above — the endpoint now *does* accept a caller-supplied
> credential, and the "is this the ingested user's own token?" check that gates
> the viewer endpoints is made per request rather than once at import, so a
> token for a third party still cannot reach their private data. The token is
> never archived, never written to `manifest.json`, and never logged. No
> response field changed; same-named uploads are now archived and identified
> distinctly (`CV.docx` / `CV-2.docx`) instead of overwriting each other.

> **Configurable UI dev-server address (2026-07-21) — no API change.** The Vite
> dev server's bind address (`UI_HOST`/`UI_PORT`) and proxy target (`API_URL`)
> are frontend tooling only. Every endpoint, path and response here is
> unchanged, and in production there is no dev server: the API serves the built
> bundle on its own host/port.

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

Documents persist under `data/documents/{tailor_id}/`, alongside a `tailor.json`
copy of the run's CV, validation result and cover letter and — for a run that
paused — a `review.json` copy of the items the person was shown. The same URL
keeps working after a restart. There is no listing or deletion endpoint yet —
the `documents` array in the tailor response is the index.

## Planned (later phases)

Every endpoint in the design doc is now implemented. Known gaps, none of which
have an endpoint yet:

- **Pending reviews do not survive a restart.** The checkpointer is an
  in-process `MemorySaver`, so a paused run's `resume` returns `409` after the
  server is restarted; `GET .../review` still serves the archived record.
  A durable checkpointer (SQLite/Postgres) would remove the caveat.
- **No listing endpoints** for profiles, runs or documents — ids come from the
  response that created them.
- **Single-user.** There is no authentication and no per-user partitioning; any
  caller can read any `profile_id` or `tailor_id` they can name.

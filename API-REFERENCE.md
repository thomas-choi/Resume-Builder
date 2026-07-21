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
| `github_username` | text | no* | Public GitHub profile to ingest |
| `free_text` | text | no* | Pasted bio/notes passthrough (also the LinkedIn-summary path) |
| `job_id` | text | no | Client-generated id for SSE progress; subscribe to `GET /ingest/{job_id}/events` before POSTing. Server generates one if omitted. Doubles as the `run_id`. |
| `profile_id` | text | no | Target profile for the result. **Existing id** → a new version is appended; **new id** → created at v1. Must be 1–64 chars of `[A-Za-z0-9_-]` (else 400). Omitted → the server mints a fresh id. Distinct from `run_id` (which is one execution). |

*At least one of `cv`, `github_username`, `free_text` is required (else 400).
Unsupported CV extensions → 400. An invalid `profile_id` → 400. Graph failure → 500.

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
(raw CV under `cv/`, `github/github.json`, `linkedin/linkedin-summary.txt`,
plus `manifest.json`) and `data/output/{run_id}/output.json`.

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

Runs the tailoring graph: job analysis → tailoring → validation.

**Request:** JSON

```json
{"profile_id": "…", "job_post": "<pasted job post text>", "version": 2}
```

`version` optional (defaults to latest). 404 unknown profile; 400 empty job post.

**Response 200:**

```json
{
  "profile_id": "…",
  "job_requirements": JobRequirements,
  "tailored_cv": TailoredCV,
  "validation": {
    "passed": bool,
    "needs_review": bool,
    "flags": [{"item", "kind", "reason", "similarity"}, …]
  }
}
```

`validation.needs_review = true` means at least one claim could not be traced
to the profile — review the flags before using the CV. Nothing is silently
dropped or auto-approved in Phase 1.

> **Phase 1.e (2026-07-21) — no API change.** Null-tolerant extraction schemas
> and item-level salvage are internal: no new fields, parameters, or status
> codes. `POST /ingest` simply stops returning 500 when a source contains an
> item the extractor legitimately left empty (e.g. a GitHub repo with no
> description).

## Planned (later phases)

- Phase 2: `POST /ingest` accepts a `linkedin_export` ZIP upload.
- Phase 3: `POST /tailor` gains `render`/`cover_letter` flags; `GET /document/{tailor_id}`.
- Phase 4: `GET /tailor/{id}/review`, `POST /tailor/{id}/resume` (server-side human-in-the-loop).

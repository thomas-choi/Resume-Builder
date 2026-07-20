# Product Guide

## What this is

An AI-powered personalized resume builder. It ingests your career sources
(CV files, GitHub, free text) into one canonical **career profile**, then —
given any job posting — generates a **tailored CV** that emphasizes relevant
experience **without fabricating anything**.

## Business flows (Phase 1)

### 1. Build your career profile

Provide any combination of:

- **CV file(s)** — `.docx` or `.pdf`
- **GitHub username** — public repos, languages, and README excerpts
- **Free text** — pasted bio or notes

The system extracts structured data from each source, merges duplicates
(e.g. the same job in your CV and on GitHub), and produces a profile with:

- experiences, projects, skills (with evidence counts), education, certifications
- a reusable 2–3 paragraph professional narrative
- **traceability**: every bullet/claim maps back to the source document it came from
- **conflicts**: when sources disagree (e.g. two different start dates), the
  disagreement is surfaced to you — never silently resolved

Each ingest is tagged with a **`run_id`** and keeps a full record of that run:
the exact files/inputs you provided are archived, alongside a copy of the
generated profile, so any result can be traced back to what produced it or
re-examined later. (Your uploaded résumés are retained on disk as part of this —
see OPERATIONS.md for the retention/privacy details.)

### 2. Review and edit the profile

Fetch the profile, fix anything (including resolving surfaced conflicts), and
save it back — every save creates a new version, so nothing is lost. The
profile is durable: re-tailoring for new job posts never re-runs ingestion.

### 3. Tailor a CV for a job post

Paste a job posting. The system:

1. extracts the job's requirements and ATS keywords,
2. selects and re-orders your most relevant experiences/projects (subset, not
   everything), mirroring the job's terminology only where your actual
   experience supports it,
3. **validates** every generated claim against your profile — anything that
   can't be traced back is flagged `needs_review` for you to approve or reject.

## Guardrails you can rely on

- No new employers, dates, titles, or skills are ever invented.
- Keyword mirroring is limited to what your profile evidences.
- Flagged claims are surfaced, not auto-approved — you are the final gate.

## Current limitations (Phase 1)

| Limitation | Planned fix |
|---|---|
| No LinkedIn ingestion (official data-export ZIP) | Phase 2 |
| Output is structured JSON only — no .docx/PDF rendering, no cover letter | Phase 3 |
| Review of flags happens client-side (API consumer's responsibility) | Phase 4 server-side human-in-the-loop |
| No web UI — API only | Phase 4 (React three-panel flow) |
| Two-column PDF CVs may extract with interleaved text | Later improvement |
| Single-user storage (local JSON files, no accounts) | By design for now |

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
- **GitHub username** — your own public repos (languages + README excerpts),
  the repos of organizations you belong to or collaborate on, **and** your
  contributions to other people's open-source projects
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

**What GitHub contributes to your profile.** Beyond the repos under your own
username, the profile picks up work you did inside organizations and pull
requests you merged into projects you don't own (e.g. a well-known open-source
framework). Those are recorded strictly as *contributions* — the merged PRs and
commit counts are your evidence, and the project itself is never presented as
yours; a README from someone else's project is deliberately not read in. Repos
you forked are ignored (forking is not evidence of work; the PR it produced is).

Two things follow from how GitHub itself works:

- **Your organizations are found even when your membership is private.** Private
  is GitHub's default, and it makes an account look like it belongs to no
  organization at all. If the configured GitHub token is *your own*, the builder
  reads your memberships and private repos directly, so company work counts. A
  token belonging to someone else is never used this way — nobody's private data
  is reachable by typing their username. If you'd rather keep private repos out
  of the profile entirely, your operator can set `GITHUB_INCLUDE_PRIVATE=false`;
  your organizations are still discovered, just not their private repos. Private
  repos that are included are marked as such, so a tailored CV never offers one
  as a portfolio link you couldn't actually share.
- **Being added to a repo is not the same as working on it.** Most people have
  been invited to many repos they never touched. An organization or collaborator
  repo only enters your profile if you actually committed to it, so the builder
  can't write achievements out of someone else's project you merely had access
  to.

Ingestion is **partial-failure tolerant**: if one item in a source can't be
read cleanly — a GitHub repo with no description, a garbled résumé entry — that
item alone is skipped and logged, and everything else still lands in your
profile. Previously a single unreadable entry failed the whole upload. Skipped
items are recorded in the run's logs, so nothing disappears silently; if a
source is *entirely* unreadable it is dropped with the rest of the run
continuing, and only a run where nothing at all could be read fails outright.

By default each ingest creates a brand-new profile. To instead fold fresh
sources into a profile you already have — say you land a new role and want to
re-ingest an updated résumé — pass that profile's id when ingesting; the result
is stored as a new version of it rather than a separate profile.

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
| Private repos and private org memberships are reachable only when the configured `GITHUB_TOKEN` is the ingested user's own | Multi-user ingestion needs a caller-supplied token (deferred credential decision) |
| An organization repo you worked on only via a non-default branch, or under a different commit email, may not be recognized as yours | Later improvement (branch-aware contribution probe) |
| Single-user storage (local JSON files, no accounts) | By design for now |

# Product Guide

## What this is

An AI-powered personalized resume builder. It ingests your career sources
(CV files, LinkedIn data export, GitHub, free text) into one canonical
**career profile**, then —
given any job posting — generates a **tailored CV** that emphasizes relevant
experience **without fabricating anything**.

## Business flows (Phases 1–3)

### 1. Build your career profile

Provide any combination of:

- **CV file(s)** — `.docx` or `.pdf`
- **LinkedIn data export** — the ZIP you download from LinkedIn (Settings →
  "Get a copy of your data"), or a single CSV from it
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

**What LinkedIn contributes to your profile.** Ask LinkedIn for a copy of your
data and upload the archive as-is; the builder reads your positions, education,
skills, certifications and the recommendations you received. Nothing is scraped
— LinkedIn's terms forbid it, and there is no read API for personal apps, so
your own export is the only way in, and it works whether your profile is public
or not. Because the export is *records* rather than prose, those fields are
taken literally: dates, employers and titles come across exactly as LinkedIn
holds them. Two things are deliberately kept in their place — the skills on
your profile are treated as skills you claim, never converted into
achievements, and a recommendation stays a quote from the person who wrote it,
never re-told as something you say about yourself.

This is also where **conflicts** earn their keep: when your CV and your LinkedIn
profile disagree about the same job — a start date a year apart, a slightly
different title — the two entries merge into one, and the disagreement is
listed for you to settle. The system never picks a side on its own.

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

Paste a job posting — the whole ad, as you copied it, no cleaning up needed —
and say which profile to use. That one request runs the whole way through:

1. **Read the posting.** The job ad is turned into a structured list of what the
   employer actually wants: must-have skills, nice-to-haves, seniority, the
   responsibilities, and the exact phrasing worth mirroring for applicant
   tracking systems.
2. **Choose what to say.** Your profile is matched against that list: a
   *subset* of your experience and projects is selected and re-ordered by
   relevance — not everything you've ever done — and bullets are re-worded
   toward the posting's language, but only where the underlying fact supports
   it. Nothing new is added.
3. **Check every claim.** Each bullet, skill, job and project in the draft is
   traced back to your profile. Exact matches and obvious re-wordings pass
   quietly; anything that can't be traced gets a second, stricter check and is
   returned to you as a **flag** explaining what couldn't be supported.
4. **Write a cover letter** (only if you asked for one) from the facts just
   selected for the CV.
5. **Render the documents** (only if you asked for them) — see step 4 below —
   unless there are flags, in which case nothing is written until you approve.

You get back the tailored CV, the flags, and the documents in one response. Your
profile is untouched by all of this, so you can tailor the same profile to as
many postings as you like; ingestion never runs again.

**What it costs you in time:** two AI calls for the CV, plus one for each claim
that needed the stricter check, plus one if you asked for a cover letter — a
typical run is a few seconds to a minute.

### 4. Get the documents (and a cover letter)

Ask for it and the same request also produces the files you actually send: a
**Word CV** and, where the server has LibreOffice, a **PDF** of it. The layout
is fixed and plain — your name and contact details, the headline and summary
written for this posting, then the selected experience, projects and skills —
and it contains only what the tailored CV already contained; the internal notes
about *why* each item was picked are never printed.

Optionally you also get a **cover letter** for the same posting, written from
the facts already selected for the CV. It is bound by the same rule as
everything else: no claim it makes is new. Two things it will not do — invent
reasons you want the job or opinions about the company, and repeat a
recommendation someone wrote about you as if you had said it yourself.

**Nothing flagged gets rendered.** If validation raised a flag, no file is
written and the response tells you why. You read the flags, and if you're happy
with them you re-run and say so explicitly — approving is a decision you make,
never a default. Documents stay downloadable afterwards by the id of that
tailoring run, so you can come back for the PDF later.

## Guardrails you can rely on

- No new employers, dates, titles, or skills are ever invented.
- Keyword mirroring is limited to what your profile evidences.
- Flagged claims are surfaced, not auto-approved — you are the final gate.
- A CV with unresolved flags is never turned into a finished document until you
  say so.

## Current limitations (Phases 1–3)

| Limitation | Planned fix |
|---|---|
| LinkedIn comes from your own data export — there is no "connect my LinkedIn" login | By design (LinkedIn's terms forbid scraping; no personal-app read API) |
| One fixed CV layout; styling is only customizable by supplying a base Word template | Later improvement (multiple layouts) |
| PDF depends on LibreOffice being installed server-side; without it you get the .docx only | By design — the .docx is the guaranteed output |
| Approving flagged claims means re-running the tailoring request (costing the AI calls again) rather than resuming where it paused | Phase 4 server-side human-in-the-loop |
| Review of flags happens client-side (API consumer's responsibility) | Phase 4 server-side human-in-the-loop |
| No web UI — API only | Phase 4 (React three-panel flow) |
| Two-column PDF CVs may extract with interleaved text | Later improvement |
| Private repos and private org memberships are reachable only when the configured `GITHUB_TOKEN` is the ingested user's own | Multi-user ingestion needs a caller-supplied token (deferred credential decision) |
| An organization repo you worked on only via a non-default branch, or under a different commit email, may not be recognized as yours | Later improvement (branch-aware contribution probe) |
| Single-user storage (local JSON files, no accounts) | By design for now |

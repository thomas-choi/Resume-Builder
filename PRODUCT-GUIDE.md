# Product Guide

## What this is

An AI-powered personalized resume builder. It ingests your career sources
(CV files, LinkedIn data export, GitHub, free text) into one canonical
**career profile**, then —
given any job posting — generates a **tailored CV** that emphasizes relevant
experience **without fabricating anything**.

## Business flows (Phases 1–4)

There is now a **web UI** as well as the API: open the service in a browser and
you get the three panels these flows describe — sources on the left, your
profile in the middle, tailoring and review on the right. Everything below can
still be done with plain HTTP calls; the UI is a front for the same endpoints.

### 1. Build your career profile

Provide any combination of:

- **CV file(s)** — `.docx` or `.pdf`
- **LinkedIn data export** — the ZIP you download from LinkedIn (Settings →
  "Get a copy of your data"), or a single CSV from it
- **GitHub username** — your own public repos (languages + README excerpts),
  the repos of organizations you belong to or collaborate on, **and** your
  contributions to other people's open-source projects
- **Free text** — pasted bio or notes

In the UI, files build up across separate picks: choose `CV.docx`, then choose
`CV.pdf`, and both are staged and listed, each with its own remove button. (A
second pick used to silently replace the first.) Two files that happen to share
a name are both kept and stay separately traceable — the second is stored as
`CV-2.docx` — so neither quietly disappears.

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
  organization at all. If the GitHub token is *your own* — either one your
  operator configured, or one you paste into the optional "GitHub token" field
  when you ingest — the builder reads your memberships and private repos
  directly, so company work counts. Pasting your own token is what lets several
  people use one shared server and each still see their own private work; the
  token is used for that one request and never stored, logged, or written to
  disk. A token for a different username only raises rate limits. A
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

Ingestion is **partial-failure tolerant, and tells you when it was partial**: if
one item in a source can't be read cleanly — a GitHub repo with no description,
a garbled résumé entry — that item alone is skipped and everything else still
lands in your profile. Previously a single unreadable entry failed the whole
upload. Anything skipped is now **listed by name in the UI** with a short
reason, and the success message says how many were skipped, so a run that
quietly lost a whole source can no longer look like a clean one. If a source is
*entirely* unreadable it is dropped with the rest of the run continuing, and
only a run where nothing at all could be read fails outright.

Your GitHub repositories are read a handful at a time rather than all at once.
That matters for a practical reason: asked for fifty repositories in a single
answer, the model sometimes returned nothing usable and the whole GitHub source
vanished from the profile. Now a problem repository costs you that repository
and nothing else — it is named in the skipped list, and the rest of your work
still arrives.

By default each ingest creates a brand-new profile. To instead fold fresh
sources into a profile you already have — say you land a new role and want to
re-ingest an updated résumé — pass that profile's id when ingesting; the result
is stored as a new version of it rather than a separate profile.

While an ingest runs you can watch it happen — each step (reading your sources,
extracting facts, merging them into a profile, saving it) reports as it
finishes, so a long GitHub or LinkedIn ingest is not a blank screen.

**A finished run empties the form; a failed one does not.** When the profile is
built, the staged files, the pasted notes, the GitHub token and the
existing-profile id are cleared, so clicking "Build profile" a second time
cannot quietly ingest the same CVs again. What stays on screen is the report of
the run that just finished — the step list, the profile id and version, and any
skipped repositories. If the run *fails*, everything you staged stays staged:
a server error should not cost you the work of picking every file again.

**Starting a new profile clears the old one's output.** The moment the active
profile changes, the review screen and the tailoring screen are emptied — you
never see the previous profile's headline or conflicts under the new profile's
id, and the previous tailored CV, its diff and its download links go with it.
Those links pointed at the earlier document; leaving them up invites
downloading the wrong CV.

**"Clear everything" starts a fresh session.** The button beside the profile
loader empties every panel at once — staged files, the GitHub token, pasted
notes, the loaded profile and its unsaved edits, the tailored CV and its
downloads. It asks for confirmation first, because unsaved profile edits are
among the things it discards. It only clears the *screen*: every profile,
run and document remains on the server and can be loaded again by id.

### 2. Review and edit the profile

Fetch the profile, fix anything, and save it back — every save creates a new
version, so nothing is lost. The profile is durable: re-tailoring for new job
posts never re-runs ingestion.

**You see everything the profile holds.** The review screen lists your contact
details, experience, projects, education, skills and certifications — not a
selection of them. This matters most for a GitHub ingest: your repositories are
stored as projects, each with its description, the languages it uses and a link
back to the repository, and until now the screen simply didn't draw them, so a
profile built from GitHub alone looked empty when it wasn't. Long lists — fifty
repositories is normal — open showing the first ten with a "Show all" control,
so the rest of the profile stays reachable.

**A network blip does not take your work with it.** If the connection drops
while you are editing — the API restarts, the VPN reconnects, the laptop
wakes — the profile stays on screen exactly as you left it, with a message
above it saying it could not be refreshed and a Retry button. Only a profile
that never loaded at all shows an error in place of the panel. When the failure
was the connection rather than the server, the message says so ("Could not
reach the API … — is the server running?") instead of blaming the page.

**Settling a conflict is a choice you record.** Where two sources disagreed,
you pick the value you consider right and it is stored with the disagreement,
not instead of it: the conflict stays on the profile, now showing what each
source said *and* what you decided. Nothing is quietly overwritten, and you can
change your mind later — the earlier version is still there.

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
4. **Stop and ask you, if anything was flagged** — see "Reviewing what was
   flagged" below. The run waits; it does not go on to write files.
5. **Write a cover letter** (only if you asked for one) from the facts that
   survived your review.
6. **Render the documents** (only if you asked for them) — see step 4 below.

You get back the tailored CV, the flags, and the documents in one response —
or, if something was flagged, the tailored CV and the decision waiting for you.
Your profile is untouched by all of this, so you can tailor the same profile to
as many postings as you like; ingestion never runs again.

Side by side with the result you can see **where each line came from**: your
original profile bullet next to the tailored version, labelled as unchanged, a
rewording, or something with no visible origin — plus the bullets that were
left out of this application entirely. The projects chosen for this application
are shown too, in the order they appear in the document, with the ones left out
named and any project that couldn't be traced back to your profile marked —
so nothing reaches the CV you approve without having been on the screen. If
your profile can't be fetched to compare against, the screen says the
comparison is missing rather than showing an empty table — an empty comparison
would read as "nothing changed", which is the wrong thing to believe about a
document you are about to approve.

**What it costs you in time:** two AI calls for the CV, plus one for each claim
that needed the stricter check, plus one if you asked for a cover letter — a
typical run is a few seconds to a minute. Answering a review costs nothing
extra: the CV is not regenerated.

### 3a. Reviewing what was flagged

When a claim can't be traced back to your profile and you asked for documents,
the run **pauses** rather than finishing. You are shown, for each flagged item:
what the CV claimed, why it couldn't be placed, and the closest thing your
profile actually says — with the source it came from. A short plain-language
explanation accompanies them, written to help you tell a harmless rewording
apart from an invented fact; it will never suggest a phrasing that would get an
unsupported claim past the check.

Then you decide, item by item: **keep it** or **remove it**. Keeping is a
deliberate act — anything you don't explicitly keep is dropped, because the
safe default for a claim nobody could verify is not to make it. Removing an
item doesn't cost you the run: that claim disappears from the CV (from the
skills list, the bullets, *and* the summary paragraph, so it can't survive by
being mentioned somewhere else) and everything else renders as normal.

The CV you approve is exactly the CV you get. Before, approving meant re-running
the whole thing, which produced a fresh draft that nobody had read; now the run
you reviewed is the run that continues.

If you would rather not be asked — you already know the flags and accept them —
you can say so when you submit the job post, and the run renders without
pausing.

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
written until you have been through the review above — approving is a decision
you make, never a default. Documents stay downloadable afterwards by the id of
that tailoring run, so you can come back for the PDF later.

## Guardrails you can rely on

- No new employers, dates, titles, or skills are ever invented.
- Keyword mirroring is limited to what your profile evidences.
- Flagged claims are surfaced, not auto-approved — you are the final gate.
- A CV with unresolved flags is never turned into a finished document until you
  say so. This is now enforced by the pipeline itself, not by the app asking
  nicely: the run physically stops until your decision arrives.
- A claim you reject is removed from the finished CV, everywhere it appears.

*(2026-07-21: making the UI dev server's IP and port configurable changed no
user-facing behaviour — it is a developer setup option, documented in
[OPERATIONS.md](OPERATIONS.md#developing-the-ui-against-a-running-api). What you
see in the browser is identical.)*

## Current limitations (Phases 1–4)

| Limitation | Planned fix |
|---|---|
| LinkedIn comes from your own data export — there is no "connect my LinkedIn" login | By design (LinkedIn's terms forbid scraping; no personal-app read API) |
| One fixed CV layout; styling is only customizable by supplying a base Word template | Later improvement (multiple layouts) |
| PDF depends on LibreOffice being installed server-side; without it you get the .docx only | By design — the .docx is the guaranteed output |
| A review left unanswered is lost if the server restarts — you'd have to tailor again (what you were asked is still on record) | Later improvement (durable checkpoint storage) |
| The summary paragraph isn't itself fact-checked. A rejected claim is scrubbed from it, but a summary that *paraphrases* something unsupported without naming it isn't caught | Later improvement (validate the prose too) |
| The cover letter is not re-checked against your profile — it is constrained by being built from the already-checked CV | Later improvement |
| Two-column PDF CVs may extract with interleaved text | Later improvement |
| Private repos and private org memberships are reachable only when the GitHub token used is the ingested user's own — but you can now supply your own per ingest, so one server serves several people | Resolved (per-request token) |
| An organization repo you worked on only via a non-default branch, or under a different commit email, may not be recognized as yours | Later improvement (branch-aware contribution probe) |
| Accounts are isolated per person (Phase 7 — see below); a shared, no-login mode is still available for single-user deployments | Resolved (per-user data roots + route enforcement) |

## Accounts (Phase 7 — passwordless sign-up / sign-in, isolated per person)

You create an account and sign in **without a password**. The email address *is*
your identity — there is no separate username to choose.

- **Sign up** with your first name, last name and email. We email you a proof of
  receipt: by default a **6-digit code** you type back into the screen, or (if
  the deployment prefers it) a **magic link** you click. Completing it confirms
  the address and signs you in.
- **Sign in** with just your email; the same code-or-link confirms it's you.
- **No login before you confirm.** An address that started sign-up but never
  finished it can't be used to sign in — the only way forward is to complete the
  sign-up it began.
- To keep the screens from revealing who has an account, every sign-up and
  sign-in reply looks the same; if there's nothing to act on (no account, or one
  you already finished), the *email* explains what to do next.
- **Signed in, then away for a while?** Sessions last 14 days and refresh as you
  use the app. If yours expires mid-session, the app quietly drops you back to
  the sign-in screen rather than showing a wall of errors — your unsaved edits on
  screen are kept until you navigate away, but a fresh sign-in is needed to save.
- **Sign out** from the top bar clears everything the session loaded, so nothing
  from your account is left on screen for the next person at that browser.

**Your workspace is now private to you.** Every profile you build, every run you
ingest and every document you tailor lives under your own account; nobody else
signed into the same server can read, edit or download them, and an id from
someone else's account simply looks "not found" to you. The path your files live
at is derived from a one-way hash of your email, so your address never appears in
a filename or a log.

**Single-user / offline deployments** can turn authentication off
(`AUTH_ENABLED=false`), which skips login entirely and puts everyone on one
shared account — handy for a personal install or CI. See OPERATIONS.md.

*Running locally with no mail server?* The default setup writes each email to a
file instead of sending it — open the newest file in `data/auth/outbox/` to read
your code or link (see OPERATIONS.md).

---

*Cloud deployment (2026-07-23) — no product change.* Running the app on a cloud
VM instead of a laptop changes where it lives, not what it does: the same
capabilities, flows and limitations described above apply verbatim. Two things
users notice indirectly — sign-in emails must go through a real mail server once
the app is shared (the file-based fallback above is a solo-install convenience),
and everything anyone uploads now lives on that VM, so the PII handling in
OPERATIONS.md → "Data management" applies to other people's résumés too. Setup
is OPERATIONS.md → "Cloud deployment (Docker Hub → VM)".

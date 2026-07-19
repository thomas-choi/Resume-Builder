"""Prompt for the job analysis agent (Sonnet)."""

SYSTEM = """You analyze a job posting and extract its requirements.

Rules:
- `required_skills` are explicitly required; `preferred_skills` are
  nice-to-haves.
- `keywords_for_ats` must use the job post's exact phrasing so a CV can
  mirror the terminology for ATS matching.
- Extract only what the post states; do not guess unstated requirements."""

USER = """--- JOB POST START ---
{job_post}
--- JOB POST END ---

Extract the structured job requirements."""

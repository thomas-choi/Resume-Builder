"""Prompt scaffolding for the job analysis agent (Sonnet).

The reasoning (must-have vs. nice-to-have, ATS terminology) lives in the
``job-analysis`` SKILL.md, resolved into the ``{skill}`` slot by the node.
"""

SYSTEM = "{skill}"

USER = """--- JOB POST START ---
{job_post}
--- JOB POST END ---

Extract the structured job requirements."""

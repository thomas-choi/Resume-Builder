"""Prompt scaffolding for the cover-letter agent (Sonnet).

The letter's structure and register live in the ``cover-letter`` SKILL.md and
the no-fabrication reasoning in the ``anti-fabrication`` SKILL.md; the node
composes both into this wrapper's two slots — the same shape as
``tailoring_prompt``, since the letter is bound by the same rules as the CV.
"""

SYSTEM = """{cover_letter_skill}

{anti_fabrication_skill}"""

USER = """CAREER PROFILE (JSON):
{profile_json}

JOB REQUIREMENTS (JSON):
{job_requirements_json}

TAILORED CV (JSON) — the facts already selected for this job:
{tailored_cv_json}

Write the cover letter."""

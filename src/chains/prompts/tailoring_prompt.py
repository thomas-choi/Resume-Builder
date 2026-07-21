"""Prompt scaffolding for the CV tailoring agent (Sonnet).

The tailoring HARD RULES live in the ``cv-tailoring`` SKILL.md and the
no-fabrication reasoning in the ``anti-fabrication`` SKILL.md; the node composes
both into this wrapper's two slots. Keeping the prior text as the skill bodies
means behavior is unchanged when the skills are present.
"""

SYSTEM = """{cv_tailoring_skill}

{anti_fabrication_skill}"""

USER = """CAREER PROFILE (JSON):
{profile_json}

JOB REQUIREMENTS (JSON):
{job_requirements_json}

Produce the tailored CV."""

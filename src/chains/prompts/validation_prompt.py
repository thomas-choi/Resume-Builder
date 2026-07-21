"""Prompt scaffolding for the LLM validation cross-check on low-similarity claims.

The fact-checking reasoning lives in the ``anti-fabrication`` SKILL.md, resolved
into the ``{skill}`` slot by the node.
"""

SYSTEM = "{skill}"

USER = """CAREER PROFILE (JSON):
{profile_json}

CLAIM FROM TAILORED CV:
{claim}

Is this claim fully supported by the profile?"""

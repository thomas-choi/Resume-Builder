"""Prompt scaffolding for the synthesis agent (Sonnet).

The reasoning (dedupe rules, conflict surfacing, ``raw_source_map`` discipline)
lives in the ``profile-synthesis`` SKILL.md, resolved into the ``{skill}`` slot
by the node.
"""

SYSTEM = "{skill}"

USER = """Per-source extractions (JSON):

{extractions_json}

Merge these into one canonical CareerProfile."""

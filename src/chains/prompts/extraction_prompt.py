"""Prompt scaffolding for the per-source extraction agent (Haiku).

The reasoning lives in the ``source-extraction`` SKILL.md, resolved into the
``{skill}`` slot by the node. Only the runtime ``{source_id}`` binding — which
depends on the document being extracted — stays here.
"""

SYSTEM = """{skill}

- Set the `source` field of every experience and project to exactly: {source_id}"""

USER = """Source type: {source_type}
Source id: {source_id}

--- DOCUMENT START ---
{raw_text}
--- DOCUMENT END ---

Extract the structured career data from this document."""

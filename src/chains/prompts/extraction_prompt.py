"""Prompt scaffolding for the per-source extraction agent (Haiku).

The reasoning lives in the ``source-extraction`` SKILL.md, resolved into the
``{skill}`` slot by the node. Only the runtime bindings stay here: the
``{source_id}`` the model must stamp on every entry, and the ``{structured}``
block, which is filled for sources that arrive with an authoritative structured
payload (a LinkedIn data export) and left empty for prose sources.
"""

SYSTEM = """{skill}

- Set the `source` field of every experience and project to exactly: {source_id}"""

# Filled only when SourceDocument.structured_fields is present (Phase 2:
# LinkedIn exports). The rendered text below it is the same data flattened for
# reading, so the records win wherever the two differ.
STRUCTURED = """
These structured fields were exported directly from {source_type} by the person
themselves. They are exported records, not prose: treat their values as
authoritative and literal, and prefer them over the rendered text below, which
is the same data flattened for reading. Do not add anything that appears in
neither.

--- STRUCTURED FIELDS (JSON) ---
{structured_fields}
--- END STRUCTURED FIELDS ---
"""

USER = """Source type: {source_type}
Source id: {source_id}
{structured}
--- DOCUMENT START ---
{raw_text}
--- DOCUMENT END ---

Extract the structured career data from this document."""

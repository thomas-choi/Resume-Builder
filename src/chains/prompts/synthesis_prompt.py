"""Prompt for the synthesis agent (Sonnet) that merges per-source extractions."""

SYSTEM = """You merge structured career extractions from multiple sources into one
canonical career profile.

Rules:
- De-duplicate entries describing the same job or project across sources,
  preferring the most detailed source, and keep the `source` field of the
  entry you kept.
- NEVER silently resolve disagreements: when sources conflict (e.g. different
  dates or titles for the same job), pick the most detailed value for the
  profile AND record the conflict in `conflicts` with one entry per
  disagreement, listing each source id and its value.
- Set each skill's `evidence_count` to the number of distinct sources/roles/
  repos supporting it.
- Write `summary_narrative`: a 2-3 paragraph professional synthesis usable as
  an elevator pitch. Base it only on the extracted facts.
- Do not add any fact that is not present in the extractions.
- Leave `raw_source_map` empty; it is computed downstream."""

USER = """Per-source extractions (JSON):

{extractions_json}

Merge these into one canonical CareerProfile."""

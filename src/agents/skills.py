"""Project adapter over ``fund_models.skills`` (the FUND skills mechanism).

Phase 1's LLM nodes are single-shot ``with_structured_output`` calls rather than
tool-calling loops, so skills are resolved **deterministically by node** instead
of via FUND's runtime ``load_skill`` tool. Each node prepends the body of its
owning ``SKILL.md`` to its system prompt.

``fund_models.skills.scan_skills`` is reused unchanged; this module only adds a
thin caching/lookup layer keyed on ``config.SKILLS_DIR``. A missing skills
directory degrades gracefully (empty context), so nodes fall back to their inline
prompt scaffolding and day-one behavior is unchanged.
"""

import functools
from pathlib import Path

from fund_models.skills import scan_skills
from src import config


@functools.lru_cache(maxsize=None)
def _scan(skills_dir: str) -> tuple[dict, ...]:
    """Cache the frontmatter registry per skills directory (path as cache key)."""
    return tuple(scan_skills(Path(skills_dir)))


def _registry() -> tuple[dict, ...]:
    """Registry for the currently configured ``SKILLS_DIR`` (re-read each call)."""
    return _scan(str(config.SKILLS_DIR))


def _read_body(path: str) -> str:
    """Return a SKILL.md body with its YAML frontmatter stripped."""
    text = Path(path).read_text()
    if text.startswith("---"):
        _, _, body = text.split("---", 2)
        return body.strip()
    return text.strip()


def resolve_skill(name: str) -> str:
    """Return the SKILL.md body (frontmatter stripped) for a node's skill.

    Args:
        name: The skill ``name`` from its YAML frontmatter (e.g. ``cv-tailoring``).

    Returns:
        The Markdown body of the skill, or ``""`` when no skills are available at
        all (missing/empty ``SKILLS_DIR``) so the calling node degrades to its
        inline prompt scaffolding.

    Raises:
        KeyError: If skills exist but none matches ``name`` (a typo/misconfig,
            not a graceful-degradation case).
    """
    registry = _registry()
    for skill in registry:
        if skill["name"] == name:
            return _read_body(skill["path"])
    if not registry:
        return ""
    available = ", ".join(s["name"] for s in registry)
    raise KeyError(f"Unknown skill {name!r}. Available skills: {available}")


def skills_catalog() -> str:
    """Return a frontmatter-only summary of every skill, for discovery.

    Mirrors ``AgentBase.get_skills_context`` formatting. Empty string when no
    skills are available.
    """
    registry = _registry()
    if not registry:
        return ""
    text = "## Available Skills\n\n"
    for skill in registry:
        text += f"### {skill['name']}\n{skill['description']}\n\n"
    return text

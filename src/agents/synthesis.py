"""synthesize_profile node — merge per-source extractions (Sonnet).

The LLM merges, dedupes, and surfaces conflicts; `raw_source_map` is then
built deterministically from the merged entries' `source` fields so the
validation gate never depends on LLM-generated traceability.
"""

import json
import logging

from src import config
from src.agents.llm import make_llm
from src.agents.skills import resolve_skill
from src.chains.prompts import synthesis_prompt
from src.models.schemas import CareerProfile, SourceExtraction

logger = logging.getLogger(__name__)


def build_raw_source_map(profile: CareerProfile) -> dict[str, str]:
    """Map every claim (bullet, project description, skill) to its source id.

    Empty claims are skipped: description-less projects (common for GitHub
    repos) would otherwise all collide on a single ``""`` key and inject a
    meaningless entry into the map the validation gate reads.
    """
    source_map: dict[str, str] = {}
    for exp in profile.experiences:
        for bullet in exp.bullets:
            if bullet:
                source_map[bullet] = exp.source
    for proj in profile.projects:
        if proj.description:
            source_map[proj.description] = proj.source
    for skill in profile.skills:
        if skill.name:
            source_map[skill.name] = "skills"
    return source_map


def synthesize(extractions: list[SourceExtraction]) -> CareerProfile:
    """Merge per-source extractions into one canonical CareerProfile."""
    llm = make_llm(config.SYNTHESIS_MODEL).with_structured_output(CareerProfile)
    extractions_json = json.dumps(
        [e.model_dump() for e in extractions], indent=2, ensure_ascii=False
    )
    logger.debug(
        "synthesize: %d extractions, payload %d chars, model=%s",
        len(extractions),
        len(extractions_json),
        config.SYNTHESIS_MODEL,
    )
    profile: CareerProfile = llm.invoke(
        [
            ("system", synthesis_prompt.SYSTEM.format(skill=resolve_skill("profile-synthesis"))),
            ("user", synthesis_prompt.USER.format(extractions_json=extractions_json)),
        ]
    )
    profile.raw_source_map = build_raw_source_map(profile)
    logger.debug(
        "synthesize: profile name=%r, %d experiences, %d projects, %d skills, "
        "%d conflicts",
        profile.name,
        len(profile.experiences),
        len(profile.projects),
        len(profile.skills),
        len(profile.conflicts),
    )
    logger.debug("synthesize: result:\n%s", profile.model_dump_json(indent=2))
    return profile

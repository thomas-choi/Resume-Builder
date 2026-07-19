"""synthesize_profile node — merge per-source extractions (Sonnet).

The LLM merges, dedupes, and surfaces conflicts; `raw_source_map` is then
built deterministically from the merged entries' `source` fields so the
validation gate never depends on LLM-generated traceability.
"""

import json

from src import config
from src.agents.llm import make_llm
from src.chains.prompts import synthesis_prompt
from src.models.schemas import CareerProfile, SourceExtraction


def build_raw_source_map(profile: CareerProfile) -> dict[str, str]:
    """Map every claim (bullet, project description, skill) to its source id."""
    source_map: dict[str, str] = {}
    for exp in profile.experiences:
        for bullet in exp.bullets:
            source_map[bullet] = exp.source
    for proj in profile.projects:
        source_map[proj.description] = proj.source
    for skill in profile.skills:
        source_map[skill.name] = "skills"
    return source_map


def synthesize(extractions: list[SourceExtraction]) -> CareerProfile:
    """Merge per-source extractions into one canonical CareerProfile."""
    llm = make_llm(config.SYNTHESIS_MODEL).with_structured_output(CareerProfile)
    extractions_json = json.dumps(
        [e.model_dump() for e in extractions], indent=2, ensure_ascii=False
    )
    profile: CareerProfile = llm.invoke(
        [
            ("system", synthesis_prompt.SYSTEM),
            ("user", synthesis_prompt.USER.format(extractions_json=extractions_json)),
        ]
    )
    profile.raw_source_map = build_raw_source_map(profile)
    return profile

"""validate_cv node — anti-fabrication gate.

Two layers (design doc §8):
(a) non-LLM: every TailoredCV bullet/skill must map to a profile entry via
    `raw_source_map`, with difflib similarity against original bullets;
(b) LLM cross-check on anything below the similarity threshold.
Flags are returned as `needs_review`; nothing is silently dropped.
"""

import difflib
import json

from pydantic import BaseModel

from src import config
from src.agents.llm import make_llm
from src.chains.prompts import validation_prompt
from src.models.schemas import (
    CareerProfile,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)


class _ClaimCheck(BaseModel):
    """Structured output of the LLM cross-check for one claim."""

    supported: bool
    reason: str


def _best_similarity(claim: str, candidates: list[str]) -> float:
    """Highest difflib ratio between the claim and any candidate string."""
    if not candidates:
        return 0.0
    return max(
        difflib.SequenceMatcher(None, claim.lower(), c.lower()).ratio()
        for c in candidates
    )


def _llm_check(profile: CareerProfile, claim: str) -> _ClaimCheck:
    llm = make_llm(config.VALIDATION_MODEL).with_structured_output(_ClaimCheck)
    return llm.invoke(
        [
            ("system", validation_prompt.SYSTEM),
            (
                "user",
                validation_prompt.USER.format(
                    profile_json=json.dumps(
                        profile.model_dump(exclude={"raw_source_map"}),
                        indent=2,
                        ensure_ascii=False,
                    ),
                    claim=claim,
                ),
            ),
        ]
    )


def validate(profile: CareerProfile, cv: TailoredCV) -> ValidationResult:
    """Check every tailored claim against the profile; flag what can't be traced."""
    flags: list[ValidationFlag] = []
    original_bullets = [b for exp in profile.experiences for b in exp.bullets]
    threshold = config.VALIDATION_SIMILARITY_THRESHOLD

    # Bullets: exact source-map hit passes; otherwise similarity, then LLM.
    for exp in cv.selected_experiences:
        for bullet in exp.bullets:
            if bullet in profile.raw_source_map:
                continue
            similarity = _best_similarity(bullet, original_bullets)
            if similarity >= threshold:
                continue
            check = _llm_check(profile, bullet)
            if not check.supported:
                flags.append(
                    ValidationFlag(
                        item=bullet,
                        kind="bullet",
                        reason=check.reason,
                        similarity=similarity,
                    )
                )

    # Skills: must be a subset of profile skill names (case-insensitive).
    profile_skills = {s.name.lower() for s in profile.skills}
    for skill in cv.highlighted_skills:
        if skill.lower() not in profile_skills:
            flags.append(
                ValidationFlag(
                    item=skill,
                    kind="skill",
                    reason="Skill not present in the career profile",
                )
            )

    # Experiences/projects must exist in the profile (company+title / name).
    profile_exp_keys = {(e.company.lower(), e.title.lower()) for e in profile.experiences}
    for exp in cv.selected_experiences:
        if (exp.company.lower(), exp.title.lower()) not in profile_exp_keys:
            flags.append(
                ValidationFlag(
                    item=f"{exp.title} at {exp.company}",
                    kind="experience",
                    reason="Experience not present in the career profile",
                )
            )
    profile_proj_names = {p.name.lower() for p in profile.projects}
    for proj in cv.selected_projects:
        if proj.name.lower() not in profile_proj_names:
            flags.append(
                ValidationFlag(
                    item=proj.name,
                    kind="project",
                    reason="Project not present in the career profile",
                )
            )

    return ValidationResult(passed=not flags, flags=flags, needs_review=bool(flags))

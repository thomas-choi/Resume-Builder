"""Tailoring nodes (Sonnet).

- ``tailor_cv``: CareerProfile + JobRequirements -> TailoredCV
- ``write_cover_letter``: the same inputs plus the tailored CV -> CoverLetter
  (design doc §1's optional second output), under the same no-fabrication rules.
"""

import json

from src import config
from src.agents.llm import make_llm
from src.agents.skills import resolve_skill
from src.chains.prompts import cover_letter_prompt, tailoring_prompt
from src.models.schemas import (
    CareerProfile,
    CoverLetter,
    JobRequirements,
    TailoredCV,
)


def _profile_json(profile: CareerProfile) -> str:
    """Serialize a profile for a prompt, minus the internal source map."""
    return json.dumps(
        profile.model_dump(exclude={"raw_source_map"}), indent=2, ensure_ascii=False
    )


def tailor(profile: CareerProfile, requirements: JobRequirements) -> TailoredCV:
    """Generate a job-targeted CV from the canonical profile."""
    llm = make_llm(config.TAILORING_MODEL).with_structured_output(TailoredCV)
    return llm.invoke(
        [
            (
                "system",
                tailoring_prompt.SYSTEM.format(
                    cv_tailoring_skill=resolve_skill("cv-tailoring"),
                    anti_fabrication_skill=resolve_skill("anti-fabrication"),
                ),
            ),
            (
                "user",
                tailoring_prompt.USER.format(
                    profile_json=_profile_json(profile),
                    job_requirements_json=json.dumps(
                        requirements.model_dump(), indent=2, ensure_ascii=False
                    ),
                ),
            ),
        ]
    )


def generate_cover_letter(
    profile: CareerProfile, requirements: JobRequirements, cv: TailoredCV
) -> CoverLetter:
    """Write a cover letter for the same posting the CV was tailored to.

    The already-tailored CV is passed in as well as the profile: it is the set
    of facts a human deemed relevant to this job, so the letter connects those
    rather than re-selecting from scratch. `relevance_notes` is internal
    reasoning and is excluded.

    Args:
        profile: The canonical career profile.
        requirements: The parsed job posting.
        cv: The tailored CV produced for that posting.

    Returns:
        The generated cover letter.
    """
    llm = make_llm(config.COVER_LETTER_MODEL).with_structured_output(CoverLetter)
    return llm.invoke(
        [
            (
                "system",
                cover_letter_prompt.SYSTEM.format(
                    cover_letter_skill=resolve_skill("cover-letter"),
                    anti_fabrication_skill=resolve_skill("anti-fabrication"),
                ),
            ),
            (
                "user",
                cover_letter_prompt.USER.format(
                    profile_json=_profile_json(profile),
                    job_requirements_json=json.dumps(
                        requirements.model_dump(), indent=2, ensure_ascii=False
                    ),
                    tailored_cv_json=json.dumps(
                        cv.model_dump(exclude={"relevance_notes"}),
                        indent=2,
                        ensure_ascii=False,
                    ),
                ),
            ),
        ]
    )

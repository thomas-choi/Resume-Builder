"""tailor_cv node — CareerProfile + JobRequirements -> TailoredCV (Sonnet)."""

import json

from src import config
from src.agents.llm import make_llm
from src.chains.prompts import tailoring_prompt
from src.models.schemas import CareerProfile, JobRequirements, TailoredCV


def tailor(profile: CareerProfile, requirements: JobRequirements) -> TailoredCV:
    """Generate a job-targeted CV from the canonical profile."""
    llm = make_llm(config.TAILORING_MODEL).with_structured_output(TailoredCV)
    return llm.invoke(
        [
            ("system", tailoring_prompt.SYSTEM),
            (
                "user",
                tailoring_prompt.USER.format(
                    profile_json=json.dumps(
                        profile.model_dump(exclude={"raw_source_map"}),
                        indent=2,
                        ensure_ascii=False,
                    ),
                    job_requirements_json=json.dumps(
                        requirements.model_dump(), indent=2, ensure_ascii=False
                    ),
                ),
            ),
        ]
    )

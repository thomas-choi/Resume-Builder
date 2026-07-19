"""analyze_job node — job post -> JobRequirements (Sonnet)."""

from src import config
from src.agents.llm import make_llm
from src.chains.prompts import job_analysis_prompt
from src.models.schemas import JobRequirements


def analyze(job_post: str) -> JobRequirements:
    """Extract structured requirements from a job posting."""
    llm = make_llm(config.TAILORING_MODEL).with_structured_output(JobRequirements)
    return llm.invoke(
        [
            ("system", job_analysis_prompt.SYSTEM),
            ("user", job_analysis_prompt.USER.format(job_post=job_post)),
        ]
    )

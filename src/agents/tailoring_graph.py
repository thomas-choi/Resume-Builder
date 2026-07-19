"""Tailoring graph: analyze_job -> tailor_cv -> validate_cv.

Phase 3 adds render_document; Phase 4 adds a human-review interrupt() with a
checkpointer before it. In Phases 1-3 human review of validation flags is
client-side (the API returns `needs_review`).
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import job_analysis, tailoring, validation
from src.models.schemas import (
    CareerProfile,
    JobRequirements,
    TailoredCV,
    ValidationResult,
)


class TailoringState(TypedDict, total=False):
    profile: CareerProfile
    job_post: str
    job_requirements: JobRequirements
    tailored_cv: TailoredCV
    validation: ValidationResult


def analyze_job(state: TailoringState) -> TailoringState:
    """Parse the job post into structured requirements."""
    return {"job_requirements": job_analysis.analyze(state["job_post"])}


def tailor_cv(state: TailoringState) -> TailoringState:
    """Generate the tailored CV from profile + requirements."""
    return {
        "tailored_cv": tailoring.tailor(state["profile"], state["job_requirements"])
    }


def validate_cv(state: TailoringState) -> TailoringState:
    """Run the anti-fabrication validation gate."""
    return {
        "validation": validation.validate(state["profile"], state["tailored_cv"])
    }


def build_tailoring_graph():
    """Compile the tailoring StateGraph."""
    graph = StateGraph(TailoringState)
    graph.add_node("analyze_job", analyze_job)
    graph.add_node("tailor_cv", tailor_cv)
    graph.add_node("validate_cv", validate_cv)
    graph.add_edge(START, "analyze_job")
    graph.add_edge("analyze_job", "tailor_cv")
    graph.add_edge("tailor_cv", "validate_cv")
    graph.add_edge("validate_cv", END)
    return graph.compile()

"""Tailoring graph: analyze_job -> tailor_cv -> validate_cv -> render_document.

Phase 3 added the optional `write_cover_letter` node (taken only when the
caller asks for one) and the terminal `render_document` node. Rendering is
gated: a run whose validation produced flags renders nothing unless the caller
approved them (`approved`), so an untraceable claim cannot silently become a
finished document.

Phase 4 adds a human-review `interrupt()` with a checkpointer between
`validate_cv` and rendering; in Phase 3 that review is client-side (the API
returns `validation.needs_review` and the caller re-runs with approval).
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import document, job_analysis, tailoring, validation
from src.models.schemas import (
    CareerProfile,
    CoverLetter,
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
    # Phase 3 — rendering
    tailor_id: str
    render: bool  # caller asked for document files
    want_cover_letter: bool  # caller asked for a cover letter
    approved: bool  # caller approved the validation flags
    cover_letter: CoverLetter
    documents: list[dict]
    render_skipped: str | None  # why nothing was rendered, if so


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


def write_cover_letter(state: TailoringState) -> TailoringState:
    """Write the optional cover letter for the same posting."""
    return {
        "cover_letter": tailoring.generate_cover_letter(
            state["profile"], state["job_requirements"], state["tailored_cv"]
        )
    }


def render_document(state: TailoringState) -> TailoringState:
    """Render the CV (+ cover letter) to .docx/.pdf, unless the gate says no."""
    if not state.get("render"):
        return {"documents": [], "render_skipped": "rendering not requested"}
    reason = document.skip_reason(state.get("validation"), state.get("approved", False))
    if reason:
        return {"documents": [], "render_skipped": reason}

    profile = state["profile"]
    documents = document.render_documents(
        state["tailor_id"],
        state["tailored_cv"],
        name=profile.name,
        contact=profile.contact,
        cover_letter=state.get("cover_letter"),
    )
    return {"documents": documents, "render_skipped": None}


def _after_validation(state: TailoringState) -> str:
    """Route to the cover-letter node only when the caller asked for one."""
    return "write_cover_letter" if state.get("want_cover_letter") else "render_document"


def build_tailoring_graph():
    """Compile the tailoring StateGraph."""
    graph = StateGraph(TailoringState)
    graph.add_node("analyze_job", analyze_job)
    graph.add_node("tailor_cv", tailor_cv)
    graph.add_node("validate_cv", validate_cv)
    graph.add_node("write_cover_letter", write_cover_letter)
    graph.add_node("render_document", render_document)
    graph.add_edge(START, "analyze_job")
    graph.add_edge("analyze_job", "tailor_cv")
    graph.add_edge("tailor_cv", "validate_cv")
    graph.add_conditional_edges(
        "validate_cv",
        _after_validation,
        ["write_cover_letter", "render_document"],
    )
    graph.add_edge("write_cover_letter", "render_document")
    graph.add_edge("render_document", END)
    return graph.compile()

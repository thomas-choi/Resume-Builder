"""Tailoring graph: analyze_job -> tailor_cv -> validate_cv -> human_review -> render.

Phase 3 added the optional `write_cover_letter` node (taken only when the
caller asks for one) and the terminal `render_document` node. Rendering is
gated: a run whose validation produced flags renders nothing unless the flagged
items were approved, so an untraceable claim cannot silently become a finished
document.

Phase 4 makes that gate a real checkpoint. `human_review` calls LangGraph's
`interrupt()` between validation and rendering, and the graph is compiled with
a checkpointer, so the run *pauses* with its state persisted instead of
returning and asking the client to re-run. The person answers per flagged item
(`POST /tailor/{tailor_id}/resume`), rejected claims are stripped from the CV,
and the very run they reviewed continues to render. The Phase 3 path still
works: a caller who passes `approved=True` up front is not interrupted.

The checkpointer is a module-level `MemorySaver` shared by every compiled
graph, because a pause must outlive the request that started it — the review
arrives on a later HTTP call. It is in-process: pending reviews do not survive
a restart (the review payload itself is persisted by `document_store`, so a
lost checkpoint costs the resume, not the record).
"""

import logging
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.agents import document, job_analysis, review, tailoring, validation
from src.models.schemas import (
    CareerProfile,
    CoverLetter,
    JobRequirements,
    ReviewDecision,
    ReviewRequest,
    TailoredCV,
    ValidationResult,
)
from src.utils import document_store

logger = logging.getLogger(__name__)

# Shared across compiled graphs so a paused run can be resumed by a later
# request (see the module docstring).
_CHECKPOINTER = MemorySaver()


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
    approved: bool  # the flagged items have been approved
    cover_letter: CoverLetter
    documents: list[dict]
    render_skipped: str | None  # why nothing was rendered, if so
    # Phase 4 — human review
    review_request: ReviewRequest  # what the human was asked
    review_decision: ReviewDecision  # what they answered


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


def prepare_review(state: TailoringState) -> TailoringState:
    """Build what a human will be asked, when the run needs asking.

    Separate from `human_review` on purpose: LangGraph re-runs an interrupted
    node **from the top** when it resumes, so anything with a side effect or a
    cost — here an LLM call for the brief, and a write to the document store —
    must happen in an earlier node that has already completed. Producing the
    `review_request` is also what tells `human_review` to pause at all.

    A no-op unless the run would otherwise render flagged claims: nothing to
    render, nothing flagged, or the flags already approved up front (Phase 3's
    client-side path) all pass straight through.
    """
    result = state.get("validation")
    if not state.get("render") or state.get("approved") or result is None:
        return {}
    if not result.needs_review:
        return {}

    tailor_id = state["tailor_id"]
    request = review.build_review_request(tailor_id, state["profile"], result)
    request.brief = review.write_brief(request, state.get("job_requirements"))
    # Persisted before pausing so the pending review is readable (and
    # auditable) independently of the in-process checkpointer.
    document_store.save_review(tailor_id, request.model_dump())
    logger.info(
        "prepare_review: tailor %s has %d flagged item(s) for review",
        tailor_id,
        len(request.items),
    )
    return {"review_request": request}


def human_review(state: TailoringState) -> TailoringState:
    """Pause until a person decides on each flagged claim (design doc §11).

    Everything costly already happened in `prepare_review`, so re-running this
    node on resume is free: `interrupt()` returns the answer the second time
    through instead of pausing again.
    """
    request = state.get("review_request")
    if request is None:  # prepare_review found nothing to ask about
        return {}

    answer = interrupt(request.model_dump())

    decision = ReviewDecision.model_validate(answer or {})
    pruned_cv, post_validation = review.apply_decision(
        state["tailored_cv"], request, decision, state["profile"]
    )
    return {
        "tailored_cv": pruned_cv,
        "validation": post_validation,
        "approved": True,  # a human has now seen every flagged item
        "review_decision": decision,
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


def _after_review(state: TailoringState) -> str:
    """Route to the cover-letter node only when the caller asked for one.

    The cover letter is written *after* review so it can only draw on claims
    that survived it — a rejected bullet must not reappear in the letter.
    """
    return "write_cover_letter" if state.get("want_cover_letter") else "render_document"


def build_tailoring_graph(checkpointer=_CHECKPOINTER):
    """Compile the tailoring StateGraph.

    Args:
        checkpointer: Where paused runs are stored. Defaults to the shared
            in-process `MemorySaver`; pass `None` to compile a graph that
            cannot be interrupted (tests of the uninterrupted path).
    """
    graph = StateGraph(TailoringState)
    graph.add_node("analyze_job", analyze_job)
    graph.add_node("tailor_cv", tailor_cv)
    graph.add_node("validate_cv", validate_cv)
    graph.add_node("prepare_review", prepare_review)
    graph.add_node("human_review", human_review)
    graph.add_node("write_cover_letter", write_cover_letter)
    graph.add_node("render_document", render_document)
    graph.add_edge(START, "analyze_job")
    graph.add_edge("analyze_job", "tailor_cv")
    graph.add_edge("tailor_cv", "validate_cv")
    graph.add_edge("validate_cv", "prepare_review")
    graph.add_edge("prepare_review", "human_review")
    graph.add_conditional_edges(
        "human_review",
        _after_review,
        ["write_cover_letter", "render_document"],
    )
    graph.add_edge("write_cover_letter", "render_document")
    graph.add_edge("render_document", END)
    return graph.compile(checkpointer=checkpointer)

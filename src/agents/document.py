"""render_document node — TailoredCV (+ CoverLetter) -> .docx/.pdf. No LLM.

Design doc §9. This agent decides **whether** to render and drives the pure
renderer in :mod:`src.tools.docx_renderer`; it never touches content.

The gate is the point: a CV whose claims the validation agent could not trace
back to the profile must not quietly become a polished document someone sends
out. So a run with flags renders only when the caller explicitly approves the
flagged items (`approved=True`). In Phase 3 that approval is client-side — the
caller has already seen `validation.flags` in the `POST /tailor` response;
Phase 4 replaces it with a LangGraph `interrupt()` so the graph itself pauses
for a human.
"""

import logging

from src.models.schemas import CoverLetter, TailoredCV, ValidationResult
from src.tools import docx_renderer
from src.utils import document_store

logger = logging.getLogger(__name__)


def skip_reason(
    validation: ValidationResult | None, approved: bool = False
) -> str | None:
    """Why rendering must not happen, or ``None`` when it may proceed.

    Args:
        validation: The validation gate's result (``None`` = gate never ran).
        approved: Whether the caller reviewed and approved the flagged items.

    Returns:
        A human-readable reason, or ``None`` if the document may be rendered.
    """
    if validation is None:
        return "validation did not run"
    if validation.needs_review and not approved:
        return (
            f"{len(validation.flags)} validation flag(s) need review; "
            "re-run with approve_flagged=true to render anyway"
        )
    return None


def render_documents(
    email: str,
    tailor_id: str,
    cv: TailoredCV,
    name: str = "",
    contact: dict[str, str] | None = None,
    cover_letter: CoverLetter | None = None,
) -> list[dict]:
    """Render the CV (and cover letter, if generated) to .docx and PDF.

    Args:
        email: The owner's user-id; documents land under its per-account root.
        tailor_id: The tailoring run id; documents land in its store directory.
        cv: The validated tailored CV.
        name: Candidate name, taken from the `CareerProfile`.
        contact: Candidate contact fields, taken from the `CareerProfile`.
        cover_letter: Optional generated cover letter.

    Returns:
        One descriptor per written file (see `document_store.list_documents`);
        PDFs are absent when LibreOffice is unavailable or disabled.
    """
    document_store.document_dir(email, tailor_id).mkdir(parents=True, exist_ok=True)

    docx_path = docx_renderer.render_cv(
        cv, document_store.document_path(email, tailor_id, "cv", "docx"), name, contact
    )
    docx_renderer.convert_to_pdf(docx_path)
    if cover_letter is not None:
        letter_path = docx_renderer.render_cover_letter(
            cover_letter,
            document_store.document_path(email, tailor_id, "cover_letter", "docx"),
            name,
            contact,
        )
        docx_renderer.convert_to_pdf(letter_path)

    documents = document_store.list_documents(email, tailor_id)
    logger.info(
        "render_document: tailor %s produced %s",
        tailor_id,
        ", ".join(d["filename"] for d in documents) or "nothing",
    )
    return documents

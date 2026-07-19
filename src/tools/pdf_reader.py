"""Deterministic PDF CV reader using pdfplumber.

Caveat: plain text extraction can interleave columns on two-column CV
layouts. Per design doc §3 this is accepted for Phase 1; layout-aware
extraction or a vision fallback is a possible later improvement.
"""

from pathlib import Path

import pdfplumber

from src.models.schemas import SourceDocument


def read_pdf(path: str | Path) -> SourceDocument:
    """Extract per-page text from a PDF file.

    Args:
        path: Path to the .pdf file.

    Returns:
        A SourceDocument with page-delimited raw text.
    """
    path = Path(path)
    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[page {i}]\n{text}")
    return SourceDocument(
        id=f"cv_pdf:{path.name}",
        source_type="cv_pdf",
        raw_text="\n\n".join(pages),
    )

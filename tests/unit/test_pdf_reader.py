"""PDF reader: per-page extraction with page markers."""

from src.tools.pdf_reader import read_pdf


def test_read_pdf_extracts_text(sample_pdf):
    doc = read_pdf(sample_pdf)
    assert doc.source_type == "cv_pdf"
    assert doc.id == "cv_pdf:resume.pdf"
    assert "[page 1]" in doc.raw_text
    assert "Alice Smith" in doc.raw_text
    assert "distributed trading backtester" in doc.raw_text

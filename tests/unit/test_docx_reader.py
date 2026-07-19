"""docx reader: heading-delimited sections, source id from filename."""

from src.tools.docx_reader import read_docx


def test_read_docx_sections_and_id(sample_docx):
    doc = read_docx(sample_docx)
    assert doc.source_type == "cv_docx"
    assert doc.id == "cv_docx:resume.docx"
    assert "## Experience" in doc.raw_text
    assert "## Skills" in doc.raw_text
    assert "Built a distributed trading backtester in Python" in doc.raw_text


def test_read_docx_skips_empty_paragraphs(sample_docx):
    doc = read_docx(sample_docx)
    assert "\n\n\n" not in doc.raw_text

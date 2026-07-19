"""Extraction agent with mocked LLM: source ids are enforced in code."""

from src.agents import extraction
from src.models.schemas import Experience, Project, SourceDocument, SourceExtraction
from tests.conftest import FakeLLM


def test_extract_one_overwrites_source(monkeypatch):
    # The LLM "forgets" to set the right source; code must overwrite it.
    llm_output = SourceExtraction(
        name="Alice Smith",
        experiences=[
            Experience(company="Acme Corp", title="Senior Engineer", source="WRONG")
        ],
        projects=[Project(name="backtester", description="engine", source="WRONG")],
    )
    fake = FakeLLM(llm_output)
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    doc = SourceDocument(
        id="cv_docx:resume.docx", source_type="cv_docx", raw_text="Alice Smith ..."
    )
    result = extraction.extract_one(doc)

    assert result.experiences[0].source == "cv_docx:resume.docx"
    assert result.projects[0].source == "cv_docx:resume.docx"
    # Prompt contained the document text and source id
    system, user = fake.calls[0][0], fake.calls[0][1]
    assert "cv_docx:resume.docx" in system[1]
    assert "Alice Smith ..." in user[1]

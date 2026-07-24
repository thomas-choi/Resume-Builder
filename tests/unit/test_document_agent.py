"""render_document node: the validation gate, and what it writes when open."""

from src import config
from src.agents import document, tailoring_graph
from src.models.schemas import (
    CoverLetter,
    Experience,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)
from src.utils import document_store
from tests.conftest import TEST_EMAIL


def _cv() -> TailoredCV:
    return TailoredCV(
        headline="Senior Backend Engineer",
        summary="A concise pitch.",
        selected_experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                bullets=["Built a distributed trading backtester in Python"],
                source="cv_docx:resume.docx",
            )
        ],
        highlighted_skills=["Python"],
    )


def _flagged() -> ValidationResult:
    return ValidationResult(
        passed=False,
        needs_review=True,
        flags=[
            ValidationFlag(item="Ran a team of 40", kind="bullet", reason="not sourced")
        ],
    )


def test_skip_reason_none_when_validation_passed():
    assert document.skip_reason(ValidationResult(passed=True)) is None


def test_skip_reason_blocks_on_flags():
    reason = document.skip_reason(_flagged())
    assert reason is not None
    assert "1 validation flag" in reason


def test_skip_reason_yields_to_explicit_approval():
    assert document.skip_reason(_flagged(), approved=True) is None


def test_skip_reason_blocks_when_validation_never_ran():
    assert document.skip_reason(None) == "validation did not run"


def test_render_documents_writes_cv_and_cover_letter(data_dir, monkeypatch):
    monkeypatch.setattr(config, "RENDER_PDF", False)  # no LibreOffice in unit tests
    documents = document.render_documents(
        TEST_EMAIL,
        "tailor-1",
        _cv(),
        name="Alice Smith",
        contact={"email": "alice@example.com"},
        cover_letter=CoverLetter(
            greeting="Dear Hiring Manager,",
            body_paragraphs=["I would like to apply."],
            closing="Sincerely,",
        ),
    )

    assert {(d["kind"], d["format"]) for d in documents} == {
        ("cv", "docx"),
        ("cover_letter", "docx"),
    }
    root = config.user_root(TEST_EMAIL)
    assert (root / "documents" / "tailor-1" / "cv.docx").exists()
    assert (root / "documents" / "tailor-1" / "cover-letter.docx").exists()
    assert all(d["size_bytes"] > 0 for d in documents)


def test_render_documents_without_cover_letter_writes_only_the_cv(data_dir, monkeypatch):
    monkeypatch.setattr(config, "RENDER_PDF", False)
    documents = document.render_documents(TEST_EMAIL, "tailor-2", _cv(), name="Alice Smith")
    assert [d["kind"] for d in documents] == ["cv"]
    assert not (
        config.user_root(TEST_EMAIL) / "documents" / "tailor-2" / "cover-letter.docx"
    ).exists()


def _state(sample_profile, **overrides) -> dict:
    state = {
        "email": TEST_EMAIL,
        "profile": sample_profile,
        "tailored_cv": _cv(),
        "validation": ValidationResult(passed=True),
        "tailor_id": "tailor-node",
        "render": True,
    }
    state.update(overrides)
    return state


def test_node_renders_when_validation_passed(data_dir, monkeypatch, sample_profile):
    monkeypatch.setattr(config, "RENDER_PDF", False)
    result = tailoring_graph.render_document(_state(sample_profile))
    assert result["render_skipped"] is None
    assert [d["kind"] for d in result["documents"]] == ["cv"]


def test_node_skips_on_validation_flags(data_dir, sample_profile):
    result = tailoring_graph.render_document(
        _state(sample_profile, validation=_flagged())
    )
    assert result["documents"] == []
    assert "need review" in result["render_skipped"]
    assert not document_store.document_dir(TEST_EMAIL, "tailor-node").exists()


def test_node_renders_flagged_run_once_approved(data_dir, monkeypatch, sample_profile):
    monkeypatch.setattr(config, "RENDER_PDF", False)
    result = tailoring_graph.render_document(
        _state(sample_profile, validation=_flagged(), approved=True)
    )
    assert result["render_skipped"] is None
    assert result["documents"]


def test_node_skips_when_render_not_requested(data_dir, sample_profile):
    result = tailoring_graph.render_document(_state(sample_profile, render=False))
    assert result["documents"] == []
    assert result["render_skipped"] == "rendering not requested"

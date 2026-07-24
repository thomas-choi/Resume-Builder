"""Graph wiring tests with node internals mocked — no LLM calls."""

import pytest

from src import config
from src.agents import extraction, ingestion_graph, synthesis, tailoring_graph
from src.agents.extraction import ExtractionResult
from src.agents import job_analysis, tailoring, validation
from src.models.schemas import (
    CoverLetter,
    JobRequirements,
    SourceDocument,
    SourceExtraction,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)
from tests.conftest import TEST_EMAIL


def test_ingestion_graph_runs_all_nodes(monkeypatch, data_dir, sample_profile):
    monkeypatch.setattr(
        extraction,
        "extract_one",
        lambda source: ExtractionResult(SourceExtraction(name="Alice Smith")),
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="Alice ...")
    state = graph.invoke({"email": TEST_EMAIL, "sources": [doc]})

    assert state["profile"].name == "Alice Smith"
    assert state["version"] == 1
    assert state["profile_id"]


def test_ingestion_graph_saves_output_copy_for_run(monkeypatch, data_dir, sample_profile):
    import json

    monkeypatch.setattr(
        extraction,
        "extract_one",
        lambda source: ExtractionResult(SourceExtraction(name="Alice Smith")),
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="Alice ...")
    state = graph.invoke({"email": TEST_EMAIL, "run_id": "run-xyz", "sources": [doc]})

    output_path = config.user_root(TEST_EMAIL) / "output" / "run-xyz" / "output.json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    assert payload["run_id"] == "run-xyz"
    assert payload["profile_id"] == state["profile_id"]
    assert payload["version"] == state["version"]
    assert payload["profile"]["name"] == "Alice Smith"


def test_ingestion_graph_skips_a_dead_source(monkeypatch, data_dir, sample_profile):
    def flaky(source):
        if source.id == "dead":
            raise RuntimeError("provider exploded")
        return ExtractionResult(SourceExtraction(name="Alice Smith"))

    monkeypatch.setattr(extraction, "extract_one", flaky)
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    state = graph.invoke(
        {
            "email": TEST_EMAIL,
            "sources": [
                SourceDocument(id="dead", source_type="github", raw_text="repos"),
                SourceDocument(id="alive", source_type="free_text", raw_text="Alice"),
            ],
        }
    )

    assert state["profile"].name == "Alice Smith"
    assert len(state["extractions"]) == 1


def test_ingestion_graph_reports_a_dead_source_as_an_error(
    monkeypatch, data_dir, sample_profile
):
    """A lost source must reach the caller, not just the log."""

    def flaky(source):
        if source.id == "dead":
            raise RuntimeError("provider exploded")
        return ExtractionResult(SourceExtraction(name="Alice Smith"))

    monkeypatch.setattr(extraction, "extract_one", flaky)
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    state = graph.invoke(
        {
            "email": TEST_EMAIL,
            "sources": [
                SourceDocument(id="dead", source_type="github", raw_text="repos"),
                SourceDocument(id="alive", source_type="free_text", raw_text="Alice"),
            ],
        }
    )

    assert state["source_errors"] == [
        {"source": "dead", "repo": None, "reason": "provider exploded"}
    ]


def test_ingestion_graph_propagates_per_repo_errors(monkeypatch, data_dir, sample_profile):
    errors = [
        {"source": "github:alice", "repo": "alice/repo-4", "reason": "no tool call"}
    ]
    monkeypatch.setattr(
        extraction,
        "extract_one",
        lambda source: ExtractionResult(
            SourceExtraction(name="Alice Smith"), errors=list(errors)
        ),
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    state = graph.invoke(
        {
            "email": TEST_EMAIL,
            "sources": [
                SourceDocument(id="github:alice", source_type="github", raw_text="r")
            ],
        }
    )

    assert state["source_errors"] == errors


def test_store_profile_prunes_the_github_archive(monkeypatch, data_dir, sample_profile):
    """Pruned archive = what reached the profile; raw archive = what GitHub said."""
    import json

    from src.utils import run_store

    original = SourceDocument(
        id="github:alice",
        source_type="github",
        raw_text="## Owned repositories\n\n### Repository: alice/good\n\n"
        "### Repository: alice/bad\n",
    )
    stored = run_store.save_source_file(
        TEST_EMAIL, "run-prune", "github", "github.json", original.model_dump_json().encode()
    )
    original.stored_path = str(stored)
    run_store.write_manifest(
        TEST_EMAIL,
        "run-prune",
        [run_store.source_entry("github", stored, b"x", source_id="github:alice")],
    )

    pruned_text = "## Owned repositories\n\n### Repository: alice/good\n"
    monkeypatch.setattr(
        extraction,
        "extract_one",
        lambda source: ExtractionResult(
            SourceExtraction(name="Alice Smith"),
            errors=[
                {"source": "github:alice", "repo": "alice/bad", "reason": "no tool call"}
            ],
            pruned_text=pruned_text,
        ),
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    ingestion_graph.build_ingestion_graph().invoke(
        {"email": TEST_EMAIL, "run_id": "run-prune", "sources": [original]}
    )

    # github.json now holds only what reached the profile...
    assert json.loads(stored.read_text())["raw_text"] == pruned_text
    # ...and the as-fetched document survives beside it, indexed in the manifest.
    raw_path = stored.parent / "github.raw.json"
    assert "alice/bad" in json.loads(raw_path.read_text())["raw_text"]
    entries = run_store.load_manifest(TEST_EMAIL, "run-prune")["sources"]
    assert [e["filename"] for e in entries] == ["github.json", "github.raw.json"]
    assert entries[1]["source_id"] == "github:alice#as-fetched"


def test_store_profile_leaves_an_unpruned_archive_alone(
    monkeypatch, data_dir, sample_profile
):
    from src.utils import run_store

    doc = SourceDocument(id="github:alice", source_type="github", raw_text="repos")
    stored = run_store.save_source_file(
        TEST_EMAIL, "run-clean", "github", "github.json", doc.model_dump_json().encode()
    )
    doc.stored_path = str(stored)
    run_store.write_manifest(TEST_EMAIL, "run-clean", [])

    monkeypatch.setattr(
        extraction,
        "extract_one",
        lambda source: ExtractionResult(SourceExtraction(name="Alice Smith")),
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    ingestion_graph.build_ingestion_graph().invoke(
        {"email": TEST_EMAIL, "run_id": "run-clean", "sources": [doc]}
    )

    # Nothing was dropped, so no second copy is written.
    assert not (stored.parent / "github.raw.json").exists()


def test_ingestion_graph_raises_when_every_source_fails(monkeypatch, data_dir):
    def dead(source):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(extraction, "extract_one", dead)

    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="Alice ...")
    with pytest.raises(RuntimeError, match="provider exploded"):
        graph.invoke({"email": TEST_EMAIL, "sources": [doc]})


def test_ingestion_graph_rejects_empty_sources(data_dir):
    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="   ")
    with pytest.raises(ValueError):
        graph.invoke({"email": TEST_EMAIL, "sources": [doc]})


def _mock_tailoring_nodes(monkeypatch, validation_result=None):
    """Mock every LLM-backed tailoring node; returns a call recorder."""
    req = JobRequirements(title="Backend Engineer")
    cv = TailoredCV(headline="Engineer", summary="Pitch.")
    letter = CoverLetter(greeting="Dear Hiring Manager,", closing="Sincerely,")
    calls: list[str] = []

    def analyze(job_post):
        calls.append("analyze")
        return req

    def tailor(profile, requirements):
        calls.append("tailor")
        return cv

    def validate(profile, cv):
        calls.append("validate")
        return validation_result or ValidationResult(passed=True)

    def cover_letter(profile, requirements, cv):
        calls.append("cover_letter")
        return letter

    monkeypatch.setattr(job_analysis, "analyze", analyze)
    monkeypatch.setattr(tailoring, "tailor", tailor)
    monkeypatch.setattr(validation, "validate", validate)
    monkeypatch.setattr(tailoring, "generate_cover_letter", cover_letter)
    return calls


def _thread(tailor_id: str) -> dict:
    """Checkpointer config — the Phase 4 graph is compiled with a MemorySaver."""
    return {"configurable": {"thread_id": tailor_id}}


def test_tailoring_graph_runs_all_nodes(monkeypatch, sample_profile):
    _mock_tailoring_nodes(monkeypatch)

    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke(
        {"email": TEST_EMAIL, "profile": sample_profile, "job_post": "A job post"},
        _thread("t-all"),
    )

    assert state["job_requirements"].title == "Backend Engineer"
    assert state["tailored_cv"].headline == "Engineer"
    assert state["validation"].passed
    # Rendering was not requested, so nothing was written.
    assert state["documents"] == []
    assert state["render_skipped"] == "rendering not requested"


def test_tailoring_graph_skips_the_cover_letter_by_default(monkeypatch, sample_profile):
    calls = _mock_tailoring_nodes(monkeypatch)
    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke(
        {"email": TEST_EMAIL, "profile": sample_profile, "job_post": "A job post"},
        _thread("t-nocl"),
    )
    assert "cover_letter" not in calls
    assert "cover_letter" not in state


def test_tailoring_graph_writes_and_renders_a_cover_letter(
    monkeypatch, data_dir, sample_profile
):
    from src import config

    monkeypatch.setattr(config, "RENDER_PDF", False)  # no LibreOffice in unit tests
    calls = _mock_tailoring_nodes(monkeypatch)

    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke(
        {
            "email": TEST_EMAIL,
            "profile": sample_profile,
            "job_post": "A job post",
            "tailor_id": "tailor-graph",
            "render": True,
            "want_cover_letter": True,
        },
        _thread("tailor-graph"),
    )

    assert calls == ["analyze", "tailor", "validate", "cover_letter"]
    assert state["cover_letter"].greeting == "Dear Hiring Manager,"
    assert {d["kind"] for d in state["documents"]} == {"cv", "cover_letter"}
    assert (config.user_root(TEST_EMAIL) / "documents" / "tailor-graph" / "cv.docx").exists()


def test_tailoring_graph_render_is_gated_by_validation_flags(
    monkeypatch, data_dir, sample_profile
):
    # Phase 4: the gate is now a pause. The run stops at human_review instead of
    # returning a skipped render, and still writes no document.
    flagged = ValidationResult(
        passed=False,
        needs_review=True,
        flags=[ValidationFlag(item="Ran a team of 40", kind="bullet", reason="unsourced")],
    )
    _mock_tailoring_nodes(monkeypatch, validation_result=flagged)
    monkeypatch.setattr(tailoring_graph.review, "write_brief", lambda *a, **k: "")

    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke(
        {
            "email": TEST_EMAIL,
            "profile": sample_profile,
            "job_post": "A job post",
            "tailor_id": "tailor-flagged",
            "render": True,
        },
        _thread("tailor-flagged"),
    )

    assert state["__interrupt__"]
    assert "documents" not in state
    assert not (
        config.user_root(TEST_EMAIL) / "documents" / "tailor-flagged" / "cv.docx"
    ).exists()


def test_tailoring_graph_does_not_pause_when_flags_pre_approved(
    monkeypatch, data_dir, sample_profile
):
    # The Phase 3 client-side path is preserved: approve_flagged up front means
    # the graph never interrupts and renders the flagged run directly.
    from src import config

    monkeypatch.setattr(config, "RENDER_PDF", False)
    flagged = ValidationResult(
        passed=False,
        needs_review=True,
        flags=[ValidationFlag(item="Ran a team of 40", kind="bullet", reason="unsourced")],
    )
    _mock_tailoring_nodes(monkeypatch, validation_result=flagged)

    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke(
        {
            "email": TEST_EMAIL,
            "profile": sample_profile,
            "job_post": "A job post",
            "tailor_id": "tailor-preapproved",
            "render": True,
            "approved": True,
        },
        _thread("tailor-preapproved"),
    )

    assert "__interrupt__" not in state
    assert [d["kind"] for d in state["documents"]] == ["cv"]

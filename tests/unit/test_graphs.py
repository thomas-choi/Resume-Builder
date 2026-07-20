"""Graph wiring tests with node internals mocked — no LLM calls."""

import pytest

from src.agents import extraction, ingestion_graph, synthesis, tailoring_graph
from src.agents import job_analysis, tailoring, validation
from src.models.schemas import (
    JobRequirements,
    SourceDocument,
    SourceExtraction,
    TailoredCV,
    ValidationResult,
)


def test_ingestion_graph_runs_all_nodes(monkeypatch, data_dir, sample_profile):
    monkeypatch.setattr(
        extraction, "extract_one", lambda source: SourceExtraction(name="Alice Smith")
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="Alice ...")
    state = graph.invoke({"sources": [doc]})

    assert state["profile"].name == "Alice Smith"
    assert state["version"] == 1
    assert state["profile_id"]


def test_ingestion_graph_saves_output_copy_for_run(monkeypatch, data_dir, sample_profile):
    import json

    monkeypatch.setattr(
        extraction, "extract_one", lambda source: SourceExtraction(name="Alice Smith")
    )
    monkeypatch.setattr(synthesis, "synthesize", lambda extractions: sample_profile)

    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="Alice ...")
    state = graph.invoke({"run_id": "run-xyz", "sources": [doc]})

    output_path = data_dir / "output" / "run-xyz" / "output.json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text())
    assert payload["run_id"] == "run-xyz"
    assert payload["profile_id"] == state["profile_id"]
    assert payload["version"] == state["version"]
    assert payload["profile"]["name"] == "Alice Smith"


def test_ingestion_graph_rejects_empty_sources(data_dir):
    graph = ingestion_graph.build_ingestion_graph()
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="   ")
    with pytest.raises(ValueError):
        graph.invoke({"sources": [doc]})


def test_tailoring_graph_runs_all_nodes(monkeypatch, sample_profile):
    req = JobRequirements(title="Backend Engineer")
    cv = TailoredCV(headline="Engineer", summary="Pitch.")
    monkeypatch.setattr(job_analysis, "analyze", lambda job_post: req)
    monkeypatch.setattr(tailoring, "tailor", lambda profile, requirements: cv)
    monkeypatch.setattr(
        validation, "validate", lambda profile, cv: ValidationResult(passed=True)
    )

    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke({"profile": sample_profile, "job_post": "A job post"})

    assert state["job_requirements"].title == "Backend Engineer"
    assert state["tailored_cv"].headline == "Engineer"
    assert state["validation"].passed

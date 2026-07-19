"""Synthesis agent: deterministic raw_source_map, conflict surfacing."""

from src.agents import synthesis
from src.models.schemas import (
    CareerProfile,
    Conflict,
    Experience,
    Skill,
    SourceExtraction,
)
from tests.conftest import FakeLLM


def test_build_raw_source_map(sample_profile):
    source_map = synthesis.build_raw_source_map(sample_profile)
    assert (
        source_map["Built a distributed trading backtester in Python"]
        == "cv_docx:resume.docx"
    )
    assert source_map["Open-source distributed backtesting engine"] == "github:alice"
    assert source_map["Python"] == "skills"


def test_synthesize_populates_source_map_and_keeps_conflicts(monkeypatch):
    merged = CareerProfile(
        name="Alice Smith",
        experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                bullets=["Built a distributed trading backtester in Python"],
                source="cv_docx:resume.docx",
            )
        ],
        skills=[Skill(name="Python", category="language", evidence_count=2)],
        summary_narrative="Alice is a senior engineer.",
        conflicts=[
            Conflict(
                field="experience.start_date",
                description="Sources disagree",
                values={"cv_docx:resume.docx": "2020", "github:alice": "2019"},
            )
        ],
        # LLM is told to leave this empty; code must fill it
        raw_source_map={},
    )
    fake = FakeLLM(merged)
    monkeypatch.setattr(synthesis, "make_llm", lambda model, **kw: fake)

    extractions = [SourceExtraction(name="Alice Smith")]
    profile = synthesis.synthesize(extractions)

    assert profile.raw_source_map[
        "Built a distributed trading backtester in Python"
    ] == "cv_docx:resume.docx"
    assert len(profile.conflicts) == 1
    # extractions were serialized into the prompt
    assert "Alice Smith" in fake.calls[0][1][1]

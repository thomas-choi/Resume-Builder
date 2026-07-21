"""Synthesis agent: deterministic raw_source_map, conflict surfacing."""

from src import config
from src.agents import synthesis
from src.models.schemas import (
    CareerProfile,
    Conflict,
    Experience,
    Project,
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


def test_raw_source_map_skips_empty_claims():
    # Description-less GitHub repos must not all collide on a single "" key
    # and inject a meaningless entry into what the validation gate reads.
    profile = CareerProfile(
        name="Alice Smith",
        experiences=[
            Experience(
                company="Acme",
                title="Engineer",
                bullets=["Real bullet", ""],
                source="cv_docx:resume.docx",
            )
        ],
        projects=[
            Project(name="repo-a", description="", source="github:alice"),
            Project(name="repo-b", description="", source="github:bob"),
            Project(name="repo-c", description="Has one", source="github:alice"),
        ],
        skills=[Skill(name="", category="language"), Skill(name="Python", category="language")],
    )

    source_map = synthesis.build_raw_source_map(profile)

    assert "" not in source_map
    assert source_map["Has one"] == "github:alice"
    assert source_map["Real bullet"] == "cv_docx:resume.docx"
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


def test_linkedin_and_cv_disagreement_is_surfaced_not_merged(monkeypatch):
    # Phase 2: the same job appears in the CV and in the LinkedIn export with
    # different dates. It must dedupe to one experience *and* keep the
    # disagreement in `conflicts` — never silently pick a side.
    merged = CareerProfile(
        name="Alice Smith",
        experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                start_date="2020",
                bullets=["Built a distributed trading backtester in Python"],
                source="cv_docx:resume.docx",
            )
        ],
        conflicts=[
            Conflict(
                field="experience.start_date",
                description="Acme Corp start date differs between the CV and LinkedIn",
                values={
                    "cv_docx:resume.docx": "2020",
                    "linkedin:export.zip": "Jan 2021",
                },
            )
        ],
    )
    fake = FakeLLM(merged)
    monkeypatch.setattr(synthesis, "make_llm", lambda model, **kw: fake)

    cv = SourceExtraction(
        name="Alice Smith",
        experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                start_date="2020",
                bullets=["Built a distributed trading backtester in Python"],
                source="cv_docx:resume.docx",
            )
        ],
    )
    linkedin = SourceExtraction(
        name="Alice Smith",
        experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                start_date="Jan 2021",
                source="linkedin:export.zip",
            )
        ],
    )
    profile = synthesis.synthesize([cv, linkedin])

    assert len(profile.experiences) == 1
    (conflict,) = profile.conflicts
    assert conflict.field == "experience.start_date"
    assert conflict.values["linkedin:export.zip"] == "Jan 2021"
    assert conflict.values["cv_docx:resume.docx"] == "2020"
    # Both extractions, with their distinct source ids, went into the prompt.
    user_prompt = fake.calls[0][1][1]
    assert "linkedin:export.zip" in user_prompt and "cv_docx:resume.docx" in user_prompt


def _synthesize_capturing(monkeypatch) -> FakeLLM:
    fake = FakeLLM(CareerProfile(name="Alice Smith"))
    monkeypatch.setattr(synthesis, "make_llm", lambda model, **kw: fake)
    synthesis.synthesize([SourceExtraction(name="Alice Smith")])
    return fake


def test_profile_synthesis_skill_body_in_system_prompt(monkeypatch):
    fake = _synthesize_capturing(monkeypatch)
    system_prompt = fake.calls[0][0][1]
    assert "You merge structured career extractions" in system_prompt
    assert "Conflict surfacing over silent resolution" in system_prompt


def test_synthesis_degrades_without_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path / "absent")
    fake = _synthesize_capturing(monkeypatch)
    assert "Conflict surfacing over silent resolution" not in fake.calls[0][0][1]

"""Validation gate — the key anti-fabrication suite.

Covers: fabricated bullet -> flagged; reworded-but-sourced -> passes;
unsourced skill -> flagged; exact bullet -> passes without any LLM call.
"""

import pytest

from src import config
from src.agents import validation
from src.agents.validation import _ClaimCheck
from src.models.schemas import Experience, Project, TailoredCV
from tests.conftest import FakeLLM


def _cv(bullets: list[str], skills: list[str] | None = None) -> TailoredCV:
    return TailoredCV(
        headline="Senior Engineer",
        summary="Pitch.",
        selected_experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                bullets=bullets,
                source="cv_docx:resume.docx",
            )
        ],
        highlighted_skills=skills or [],
    )


def _forbid_llm(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("LLM should not be called for this case")

    monkeypatch.setattr(validation, "make_llm", _boom)


def test_exact_bullet_passes_without_llm(monkeypatch, sample_profile):
    _forbid_llm(monkeypatch)
    cv = _cv(["Built a distributed trading backtester in Python"], ["Python"])
    result = validation.validate(sample_profile, cv)
    assert result.passed and not result.needs_review


def test_reworded_but_sourced_bullet_passes(monkeypatch, sample_profile):
    _forbid_llm(monkeypatch)  # high similarity, so no LLM cross-check needed
    cv = _cv(["Developed a distributed trading backtester using Python"])
    result = validation.validate(sample_profile, cv)
    assert result.passed


def test_fabricated_bullet_is_flagged(monkeypatch, sample_profile):
    fake = FakeLLM(_ClaimCheck(supported=False, reason="No such achievement in profile"))
    monkeypatch.setattr(validation, "make_llm", lambda model, **kw: fake)
    cv = _cv(["Managed a 50-person Kubernetes platform org at Google"])
    result = validation.validate(sample_profile, cv)
    assert not result.passed and result.needs_review
    assert result.flags[0].kind == "bullet"
    assert result.flags[0].similarity is not None
    assert len(fake.calls) == 1  # LLM cross-check ran exactly once


def test_low_similarity_but_llm_supported_passes(monkeypatch, sample_profile):
    fake = FakeLLM(_ClaimCheck(supported=True, reason="Entailed by the profile"))
    monkeypatch.setattr(validation, "make_llm", lambda model, **kw: fake)
    cv = _cv(["Brings distributed systems experience to trading infrastructure"])
    result = validation.validate(sample_profile, cv)
    assert result.passed


def test_unsourced_skill_is_flagged(monkeypatch, sample_profile):
    _forbid_llm(monkeypatch)  # skill check is deterministic
    cv = _cv(["Built a distributed trading backtester in Python"], ["Kubernetes"])
    result = validation.validate(sample_profile, cv)
    assert not result.passed
    assert result.flags[0].kind == "skill" and result.flags[0].item == "Kubernetes"


def test_skill_check_is_case_insensitive(monkeypatch, sample_profile):
    _forbid_llm(monkeypatch)
    cv = _cv(["Built a distributed trading backtester in Python"], ["python"])
    result = validation.validate(sample_profile, cv)
    assert result.passed


def test_fabricated_experience_is_flagged(monkeypatch, sample_profile):
    _forbid_llm(monkeypatch)
    cv = TailoredCV(
        headline="Engineer",
        summary="Pitch.",
        selected_experiences=[
            Experience(
                company="Google",
                title="Staff Engineer",
                bullets=[],
                source="cv_docx:resume.docx",
            )
        ],
    )
    result = validation.validate(sample_profile, cv)
    assert not result.passed
    assert result.flags[0].kind == "experience"


def test_fabricated_project_is_flagged(monkeypatch, sample_profile):
    _forbid_llm(monkeypatch)
    cv = TailoredCV(
        headline="Engineer",
        summary="Pitch.",
        selected_projects=[
            Project(name="quantlib-x", description="made up", source="github:alice")
        ],
    )
    result = validation.validate(sample_profile, cv)
    assert not result.passed
    assert result.flags[0].kind == "project"


@pytest.mark.parametrize(
    "claim,expected_min",
    [("Developed a distributed trading backtester using Python", 0.55)],
)
def test_best_similarity_sanity(sample_profile, claim, expected_min):
    bullets = [b for e in sample_profile.experiences for b in e.bullets]
    assert validation._best_similarity(claim, bullets) >= expected_min


def test_anti_fabrication_skill_body_in_cross_check_prompt(monkeypatch, sample_profile):
    fake = FakeLLM(_ClaimCheck(supported=True, reason="ok"))
    monkeypatch.setattr(validation, "make_llm", lambda model, **kw: fake)
    # A low-similarity claim forces the LLM cross-check, exercising the prompt.
    validation.validate(sample_profile, _cv(["Ran a national logistics program"]))
    system_prompt = fake.calls[0][0][1]
    assert "strict fact-checker for tailored CVs" in system_prompt
    assert "Bias toward flagging" in system_prompt


def test_validation_cross_check_degrades_without_skills(monkeypatch, sample_profile):
    monkeypatch.setattr(config, "SKILLS_DIR", "/nonexistent/skills")
    fake = FakeLLM(_ClaimCheck(supported=True, reason="ok"))
    monkeypatch.setattr(validation, "make_llm", lambda model, **kw: fake)
    result = validation.validate(sample_profile, _cv(["Ran a national logistics program"]))
    assert result.passed  # LLM said supported; call worked with skill absent
    assert "strict fact-checker" not in fake.calls[0][0][1]

"""Schema construction, defaults, and JSON round-trips."""

import pytest
from pydantic import ValidationError

from src.models.schemas import (
    CareerProfile,
    Experience,
    JobRequirements,
    Project,
    Skill,
    SourceDocument,
    TailoredCV,
    ValidationResult,
)


def test_source_document_minimal():
    doc = SourceDocument(id="free_text", source_type="free_text", raw_text="hi")
    assert doc.structured_fields is None


def test_career_profile_defaults():
    profile = CareerProfile(name="Alice")
    assert profile.experiences == []
    assert profile.conflicts == []
    assert profile.raw_source_map == {}


def test_profile_json_round_trip(sample_profile):
    restored = CareerProfile.model_validate_json(sample_profile.model_dump_json())
    assert restored == sample_profile
    assert restored.conflicts[0].values["github:alice"] == "2019"


def test_experience_requires_source():
    exp = Experience(company="Acme", title="Engineer", source="cv_docx:x.docx")
    assert exp.bullets == []


def test_tailored_cv_and_validation_defaults():
    cv = TailoredCV(headline="Engineer", summary="A summary.")
    assert cv.selected_experiences == []
    result = ValidationResult(passed=True)
    assert result.flags == [] and result.needs_review is False


def test_job_requirements_defaults():
    req = JobRequirements(title="Backend Engineer")
    assert req.required_skills == [] and req.company is None


# --- null tolerance on extraction-facing models (Phase 1.e) ---


def test_project_tolerates_null_description():
    # A GitHub repo with no description: the extractor emits null, not a string.
    proj = Project(name="backtester", description=None, source="github:alice")
    assert proj.description == ""
    assert Project(name="backtester", source="github:alice").description == ""


def test_project_and_experience_tolerate_null_lists():
    exp = Experience(company="Acme", title="Engineer", bullets=None, source="cv:x")
    assert exp.bullets == []
    proj = Project(name="p", technologies=None, source="github:alice")
    assert proj.technologies == []


def test_null_strings_become_blank_across_extraction_models():
    exp = Experience(company=None, title=None, source=None)
    assert (exp.company, exp.title, exp.source) == ("", "", "")
    skill = Skill(name=None, category=None)
    assert (skill.name, skill.category) == ("", "")


def test_job_requirements_tolerates_null_lists():
    req = JobRequirements(
        title="Backend Engineer",
        required_skills=None,
        preferred_skills=None,
        responsibilities=None,
        keywords_for_ats=None,
    )
    assert req.required_skills == []
    assert req.preferred_skills == []
    assert req.responsibilities == []
    assert req.keywords_for_ats == []


def test_non_extraction_models_stay_strict():
    # A null here is a real bug, not a sparse source — it must still raise.
    with pytest.raises(ValidationError):
        TailoredCV(headline=None, summary="A summary.")
    with pytest.raises(ValidationError):
        TailoredCV(headline="Engineer", summary="A summary.", highlighted_skills=None)

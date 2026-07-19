"""Schema construction, defaults, and JSON round-trips."""

from src.models.schemas import (
    CareerProfile,
    Experience,
    JobRequirements,
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

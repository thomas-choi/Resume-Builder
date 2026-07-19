"""Job analysis + tailoring agents with mocked LLMs."""

from src.agents import job_analysis, tailoring
from src.models.schemas import Experience, JobRequirements, TailoredCV
from tests.conftest import FakeLLM


def test_analyze_job(monkeypatch):
    req = JobRequirements(
        title="Backend Engineer",
        required_skills=["Python"],
        keywords_for_ats=["distributed systems"],
    )
    fake = FakeLLM(req)
    monkeypatch.setattr(job_analysis, "make_llm", lambda model, **kw: fake)
    result = job_analysis.analyze("We need a backend engineer with Python...")
    assert result.title == "Backend Engineer"
    assert "backend engineer with Python" in fake.calls[0][1][1]


def test_tailor_passes_profile_and_requirements(monkeypatch, sample_profile):
    cv = TailoredCV(
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
    fake = FakeLLM(cv)
    monkeypatch.setattr(tailoring, "make_llm", lambda model, **kw: fake)

    req = JobRequirements(title="Backend Engineer", required_skills=["Python"])
    result = tailoring.tailor(sample_profile, req)

    assert result.highlighted_skills == ["Python"]
    user_prompt = fake.calls[0][1][1]
    # Both profile facts and job requirements reached the prompt
    assert "Acme Corp" in user_prompt
    assert "Backend Engineer" in user_prompt
    # raw_source_map is internal and excluded from the prompt
    assert "raw_source_map" not in user_prompt

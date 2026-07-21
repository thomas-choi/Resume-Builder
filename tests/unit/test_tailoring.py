"""Job analysis + tailoring agents with mocked LLMs."""

from src import config
from src.agents import job_analysis, tailoring
from src.models.schemas import Experience, JobRequirements, TailoredCV
from tests.conftest import FakeLLM


def _tailored_cv() -> TailoredCV:
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
    # job-analysis skill body is composed into the system prompt.
    system_prompt = fake.calls[0][0][1]
    assert "You analyze a job posting" in system_prompt
    assert "Must-have vs. nice-to-have" in system_prompt


def test_job_analysis_degrades_without_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path / "absent")
    fake = FakeLLM(JobRequirements(title="Backend Engineer"))
    monkeypatch.setattr(job_analysis, "make_llm", lambda model, **kw: fake)
    result = job_analysis.analyze("We need a backend engineer...")
    assert result.title == "Backend Engineer"
    assert "Must-have vs. nice-to-have" not in fake.calls[0][0][1]


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


def test_tailoring_composes_both_skills(monkeypatch, sample_profile):
    fake = FakeLLM(_tailored_cv())
    monkeypatch.setattr(tailoring, "make_llm", lambda model, **kw: fake)
    tailoring.tailor(sample_profile, JobRequirements(title="Backend Engineer"))
    system_prompt = fake.calls[0][0][1]
    # tailoring composes cv-tailoring + anti-fabrication into one system prompt.
    assert "HARD RULES" in system_prompt  # cv-tailoring
    assert "strict fact-checker for tailored CVs" in system_prompt  # anti-fabrication


def test_tailoring_degrades_without_skills(monkeypatch, sample_profile):
    monkeypatch.setattr(config, "SKILLS_DIR", "/nonexistent/skills")
    fake = FakeLLM(_tailored_cv())
    monkeypatch.setattr(tailoring, "make_llm", lambda model, **kw: fake)
    result = tailoring.tailor(sample_profile, JobRequirements(title="Backend Engineer"))
    assert result.highlighted_skills == ["Python"]
    system_prompt = fake.calls[0][0][1]
    assert "HARD RULES" not in system_prompt
    assert "strict fact-checker" not in system_prompt

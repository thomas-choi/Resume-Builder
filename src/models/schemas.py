"""Pydantic schemas shared across the ingestion and tailoring pipelines.

Mirrors TECHNICAL-DESIGN.md §4 (CareerProfile), §6 (JobRequirements),
§7 (TailoredCV), plus ValidationResult and conflict surfacing.
"""

from pydantic import BaseModel, Field


class SourceDocument(BaseModel):
    """Normalized raw input from one career source, before any LLM sees it."""

    id: str
    source_type: str  # "cv_docx" | "cv_pdf" | "github" | "free_text" | "linkedin"
    raw_text: str
    structured_fields: dict | None = None


class Experience(BaseModel):
    company: str
    title: str
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)  # verbatim-ish, not embellished
    source: str  # source document id, e.g. "cv_docx:resume.docx"


class Project(BaseModel):
    name: str
    description: str
    technologies: list[str] = Field(default_factory=list)
    role: str | None = None
    url: str | None = None
    source: str


class Skill(BaseModel):
    name: str
    category: str  # "language" | "framework" | "domain" | "tool"
    evidence_count: int = 1  # how many sources/repos/roles support this


class Conflict(BaseModel):
    """A cross-source disagreement surfaced to the user, never silently resolved."""

    field: str  # e.g. "experience.start_date"
    description: str
    values: dict[str, str]  # source doc id -> conflicting value


class SourceExtraction(BaseModel):
    """Structured output of the per-source extraction agent."""

    name: str | None = None
    headline: str | None = None
    contact: dict[str, str] = Field(default_factory=dict)
    experiences: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)


class CareerProfile(BaseModel):
    name: str
    headline: str | None = None
    contact: dict[str, str] = Field(default_factory=dict)
    experiences: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    summary_narrative: str = ""  # 2-3 paragraph human-readable synthesis
    raw_source_map: dict[str, str] = Field(default_factory=dict)  # claim -> source doc id
    conflicts: list[Conflict] = Field(default_factory=list)


class JobRequirements(BaseModel):
    title: str
    company: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    seniority: str | None = None
    keywords_for_ats: list[str] = Field(default_factory=list)


class TailoredCV(BaseModel):
    headline: str
    summary: str  # 2-4 sentences, job-specific framing
    selected_experiences: list[Experience] = Field(default_factory=list)
    selected_projects: list[Project] = Field(default_factory=list)
    highlighted_skills: list[str] = Field(default_factory=list)
    relevance_notes: dict[str, str] = Field(default_factory=dict)  # internal, not rendered


class ValidationFlag(BaseModel):
    """One tailored claim that could not be traced back to the profile."""

    item: str  # the bullet/skill text that was flagged
    kind: str  # "bullet" | "skill" | "experience" | "project"
    reason: str
    similarity: float | None = None


class ValidationResult(BaseModel):
    passed: bool
    flags: list[ValidationFlag] = Field(default_factory=list)
    needs_review: bool = False


class CoverLetter(BaseModel):
    """Phase 3 output schema; defined now so the API contract is stable."""

    greeting: str
    body_paragraphs: list[str] = Field(default_factory=list)
    closing: str

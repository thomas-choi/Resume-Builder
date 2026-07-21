"""Pydantic schemas shared across the ingestion and tailoring pipelines.

Mirrors TECHNICAL-DESIGN.md §4 (CareerProfile), §6 (JobRequirements),
§7 (TailoredCV), plus ValidationResult and conflict surfacing.

Extraction-facing models coerce `null` to an empty value on the fields the
extractor may legitimately omit: the anti-fabrication skill tells the model to
leave absent fields empty rather than invent them, so the schema — not the
skill — is what yields. Models that are not LLM-extraction targets
(`TailoredCV`, `ValidationFlag`, `ValidationResult`, `CoverLetter`) stay
strict, because a `null` there is a real bug.
"""

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


def _blank_if_none(v: Any) -> Any:
    """Coerce an extractor-emitted `null` into an empty string."""
    return "" if v is None else v


def _empty_if_none(v: Any) -> Any:
    """Coerce an extractor-emitted `null` into an empty list."""
    return [] if v is None else v


NullableStr = Annotated[str, BeforeValidator(_blank_if_none)]
NullableList = Annotated[list[str], BeforeValidator(_empty_if_none)]


class SourceDocument(BaseModel):
    """Normalized raw input from one career source, before any LLM sees it."""

    id: str
    source_type: str  # "cv_docx" | "cv_pdf" | "github" | "free_text" | "linkedin"
    raw_text: str
    structured_fields: dict | None = None
    stored_path: str | None = None  # archived raw file under data/sources/{run_id}/


class Experience(BaseModel):
    company: NullableStr
    title: NullableStr
    start_date: str | None = None
    end_date: str | None = None
    location: str | None = None
    # verbatim-ish, not embellished
    bullets: NullableList = Field(default_factory=list)
    source: NullableStr  # source document id, e.g. "cv_docx:resume.docx"


class Project(BaseModel):
    name: NullableStr
    description: NullableStr = ""  # GitHub repos often have none
    technologies: NullableList = Field(default_factory=list)
    role: str | None = None
    url: str | None = None
    source: NullableStr


class Skill(BaseModel):
    name: NullableStr
    category: NullableStr  # "language" | "framework" | "domain" | "tool"
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
    required_skills: NullableList = Field(default_factory=list)
    preferred_skills: NullableList = Field(default_factory=list)
    responsibilities: NullableList = Field(default_factory=list)
    seniority: str | None = None
    keywords_for_ats: NullableList = Field(default_factory=list)


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

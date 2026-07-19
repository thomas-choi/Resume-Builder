"""Shared fixtures: sample docx/pdf files, fake LLMs, tmp data dir."""

import io

import pytest
from docx import Document

from src import config
from src.models.schemas import (
    CareerProfile,
    Conflict,
    Experience,
    Project,
    Skill,
)


class FakeLLM:
    """Stands in for ChatAnthropic().with_structured_output(...).

    `responses` may be a single object (returned every call), a list
    (returned in order), or a callable(messages) -> object.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls: list = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        if callable(self.responses):
            return self.responses(messages)
        if isinstance(self.responses, list):
            return self.responses.pop(0)
        return self.responses


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the profile store at a temporary directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def build_sample_docx() -> bytes:
    doc = Document()
    doc.add_heading("Alice Smith", level=0)
    doc.add_paragraph("alice@example.com | London")
    doc.add_heading("Experience", level=1)
    doc.add_paragraph("Senior Engineer, Acme Corp, 2020-2024")
    doc.add_paragraph("Built a distributed trading backtester in Python")
    doc.add_heading("Skills", level=1)
    doc.add_paragraph("Python, PostgreSQL, Docker")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_minimal_pdf(lines: list[str]) -> bytes:
    """Hand-roll a minimal single-page PDF that pdfplumber can read."""
    parts = ["BT /F1 12 Tf 72 720 Td 14 TL"]
    for i, line in enumerate(lines):
        esc = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({esc}) Tj" if i == 0 else f"T* ({esc}) Tj")
    parts.append("ET")
    stream = "\n".join(parts).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref_pos,
    )
    return bytes(out)


@pytest.fixture
def sample_docx(tmp_path):
    path = tmp_path / "resume.docx"
    path.write_bytes(build_sample_docx())
    return path


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "resume.pdf"
    path.write_bytes(
        build_minimal_pdf(
            [
                "Alice Smith",
                "Senior Engineer, Acme Corp, 2020-2024",
                "Built a distributed trading backtester in Python",
            ]
        )
    )
    return path


@pytest.fixture
def sample_profile() -> CareerProfile:
    from src.agents.synthesis import build_raw_source_map

    profile = CareerProfile(
        name="Alice Smith",
        headline="Senior Engineer",
        contact={"email": "alice@example.com"},
        experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                start_date="2020",
                end_date="2024",
                bullets=[
                    "Built a distributed trading backtester in Python",
                    "Led migration of the data pipeline to PostgreSQL",
                ],
                source="cv_docx:resume.docx",
            )
        ],
        projects=[
            Project(
                name="backtester",
                description="Open-source distributed backtesting engine",
                technologies=["Python"],
                source="github:alice",
            )
        ],
        skills=[
            Skill(name="Python", category="language", evidence_count=2),
            Skill(name="PostgreSQL", category="tool", evidence_count=1),
        ],
        certifications=[],
        summary_narrative="Alice is a senior engineer.",
        conflicts=[
            Conflict(
                field="experience.start_date",
                description="CV and GitHub disagree on start date",
                values={"cv_docx:resume.docx": "2020", "github:alice": "2019"},
            )
        ],
    )
    profile.raw_source_map = build_raw_source_map(profile)
    return profile

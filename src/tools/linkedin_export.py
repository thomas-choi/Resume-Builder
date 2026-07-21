"""Deterministic LinkedIn reader — the official data export only, never scraping.

LinkedIn's ToS blocks scraping and there is no public profile-read API for
personal apps, so the only supported input is the archive the person downloads
themselves (Settings → "Get a copy of your data"), or the individual CSVs from
it (design doc §3, PLAN.md Phase 2).

Parsing is pure Python — no LLM — and produces one :class:`SourceDocument` per
uploaded export carrying **both** representations of the same data:

- ``structured_fields`` — the exported rows verbatim, grouped by section
  (``profile``, ``positions``, ``education``, ``skills``, ``certifications``,
  ``recommendations_received``). These are records, not prose, so the extraction
  prompt treats them as authoritative.
- ``raw_text`` — a deterministic Markdown rendering of the same sections, so the
  document reads like every other source and can be archived/diffed by eye.

Two quirks of the real export shape the parser: LinkedIn prefixes some CSVs with
a free-text ``Notes:`` preamble before the header row, and file names vary
slightly across export versions (``Recommendations_Received.csv`` vs
``Recommendations Received.csv``). Both are handled by normalizing file stems and
locating the header row by its columns rather than assuming line 1.
"""

import csv
import io
import logging
import re
import zipfile
from pathlib import Path

from src.models.schemas import SourceDocument

logger = logging.getLogger(__name__)

# Canonical section key -> accepted (normalized) export file stems.
_SECTION_STEMS: dict[str, tuple[str, ...]] = {
    "profile": ("profile",),
    "positions": ("positions",),
    "education": ("education",),
    "skills": ("skills",),
    "certifications": ("certifications",),
    "recommendations_received": ("recommendations_received", "recommendations"),
}
_SECTION_BY_STEM = {
    stem: key for key, stems in _SECTION_STEMS.items() for stem in stems
}

# Columns that identify a section's header row (lowercased). At least one must
# appear, which is what lets us skip LinkedIn's "Notes:" preamble lines.
_HEADER_HINTS: dict[str, set[str]] = {
    "profile": {"first name", "headline", "summary"},
    "positions": {"company name", "title"},
    "education": {"school name", "degree name"},
    "skills": {"name"},
    "certifications": {"name", "authority"},
    "recommendations_received": {"text", "first name"},
}


def read_linkedin_export(path: str | Path) -> SourceDocument:
    """Parse a LinkedIn data export into one :class:`SourceDocument`.

    Args:
        path: Path to the official export ``.zip``, or to a single ``.csv``
            taken from it (e.g. ``Positions.csv``).

    Returns:
        A SourceDocument with ``source_type="linkedin"``, the parsed rows in
        ``structured_fields``, and a Markdown rendering in ``raw_text``.

    Raises:
        ValueError: If the file type is unsupported, the archive is unreadable,
            or it contains no recognizable export section. Callers surface this
            as a 400 rather than ingesting an empty source.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        files = _read_zip(path)
    elif suffix == ".csv":
        files = {path.name: path.read_bytes()}
    else:
        raise ValueError(
            f"unsupported LinkedIn export file type: {suffix or '(none)'} "
            "(expected .zip or .csv)"
        )

    sections = _parse_sections(files)
    if not sections:
        raise ValueError(
            "no recognized LinkedIn export sections found (expected Positions.csv, "
            "Education.csv, Skills.csv, Certifications.csv or "
            "Recommendations_Received.csv)"
        )
    logger.debug(
        "linkedin[%s]: parsed sections %s",
        path.name,
        {k: (1 if isinstance(v, dict) else len(v)) for k, v in sections.items()},
    )
    return SourceDocument(
        id=f"linkedin:{path.name}",
        source_type="linkedin",
        raw_text=_render(path.name, sections),
        structured_fields=sections,
    )


def _read_zip(path: Path) -> dict[str, bytes]:
    """Read every CSV member of the export archive into memory."""
    try:
        with zipfile.ZipFile(path) as archive:
            return {
                info.filename: archive.read(info)
                for info in archive.infolist()
                if not info.is_dir()
                and info.filename.lower().endswith(".csv")
                and not info.filename.startswith("__MACOSX/")
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a readable ZIP archive: {exc}") from exc


def _normalize(stem: str) -> str:
    """Normalize an export file stem so naming variants map to one section."""
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def _decode(data: bytes) -> str:
    """Decode CSV bytes, tolerating the BOM LinkedIn sometimes writes."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _parse_sections(files: dict[str, bytes]) -> dict:
    """Group the recognized export CSVs into canonical sections."""
    sections: dict = {}
    for name, data in files.items():
        key = _SECTION_BY_STEM.get(_normalize(Path(name).stem))
        if key is None:
            logger.debug("linkedin: ignoring unrecognized export file %s", name)
            continue
        rows = _parse_csv(_decode(data), _HEADER_HINTS[key])
        if not rows:
            continue
        if key == "profile":
            sections[key] = rows[0]
        else:
            sections.setdefault(key, []).extend(rows)
    return sections


def _parse_csv(text: str, header_hints: set[str]) -> list[dict]:
    """Parse one export CSV into dict rows, skipping any ``Notes:`` preamble.

    The header row is located by its columns rather than assumed to be line 1,
    because LinkedIn prefixes several files with an explanatory note. Blank
    cells are dropped so absent fields stay absent (they are never invented
    downstream).
    """
    rows = list(csv.reader(io.StringIO(text)))
    header_index = next(
        (
            i
            for i, row in enumerate(rows)
            if header_hints & {cell.strip().lower() for cell in row}
        ),
        None,
    )
    if header_index is None:
        return []
    header = [cell.strip() for cell in rows[header_index]]
    parsed: list[dict] = []
    for row in rows[header_index + 1 :]:
        record = {
            column: row[i].strip()
            for i, column in enumerate(header)
            if i < len(row) and row[i].strip()
        }
        if record:
            parsed.append(record)
    return parsed


def _get(record: dict, *columns: str) -> str:
    """Case-insensitive lookup of the first present column."""
    lowered = {k.strip().lower(): v for k, v in record.items()}
    for column in columns:
        value = lowered.get(column.lower())
        if value:
            return value
    return ""


def _date_range(record: dict) -> str:
    """Render an exported start/end pair; an open end means the role is current."""
    start = _get(record, "Started On", "Start Date")
    end = _get(record, "Finished On", "End Date")
    if not start and not end:
        return ""
    return f"{start or '?'} – {end or 'Present'}"


def _render(filename: str, sections: dict) -> str:
    """Render parsed sections as Markdown, mirroring the other source readers."""
    lines: list[str] = [f"# LinkedIn data export ({filename})"]
    renderers = (
        ("profile", "Profile", _render_profile),
        ("positions", "Positions", _render_positions),
        ("education", "Education", _render_education),
        ("skills", "Skills", _render_skills),
        ("certifications", "Certifications", _render_certifications),
        (
            "recommendations_received",
            "Recommendations received (written by other people)",
            _render_recommendations,
        ),
    )
    for key, heading, renderer in renderers:
        payload = sections.get(key)
        if not payload:
            continue
        lines.append(f"\n## {heading}")
        lines.extend(renderer(payload))
    return "\n".join(lines).strip()


def _render_profile(profile: dict) -> list[str]:
    first = _get(profile, "First Name")
    last = _get(profile, "Last Name")
    lines = []
    if first or last:
        lines.append(f"Name: {' '.join(p for p in (first, last) if p)}")
    for label, column in (
        ("Headline", "Headline"),
        ("Location", "Geo Location"),
        ("Industry", "Industry"),
        ("Websites", "Websites"),
    ):
        value = _get(profile, column)
        if value:
            lines.append(f"{label}: {value}")
    summary = _get(profile, "Summary")
    if summary:
        lines.append(f"Summary: {summary}")
    return lines


def _render_positions(positions: list[dict]) -> list[str]:
    lines = []
    for position in positions:
        title = _get(position, "Title")
        company = _get(position, "Company Name")
        lines.append(f"\n### {' — '.join(p for p in (title, company) if p) or 'Position'}")
        dates = _date_range(position)
        if dates:
            lines.append(f"Dates: {dates}")
        location = _get(position, "Location")
        if location:
            lines.append(f"Location: {location}")
        description = _get(position, "Description")
        if description:
            lines.append(f"Description: {description}")
    return lines


def _render_education(education: list[dict]) -> list[str]:
    lines = []
    for entry in education:
        lines.append(f"\n### {_get(entry, 'School Name') or 'Education'}")
        degree = _get(entry, "Degree Name")
        if degree:
            lines.append(f"Degree: {degree}")
        dates = _date_range(entry)
        if dates:
            lines.append(f"Dates: {dates}")
        notes = _get(entry, "Notes", "Activities")
        if notes:
            lines.append(f"Notes: {notes}")
    return lines


def _render_skills(skills: list[dict]) -> list[str]:
    return [f"- {_get(skill, 'Name')}" for skill in skills if _get(skill, "Name")]


def _render_certifications(certifications: list[dict]) -> list[str]:
    """Certifications get issued/expires rather than a range.

    An open ``Finished On`` here means "no expiry", not "ongoing", so
    :func:`_date_range`'s "Present" would misread the record.
    """
    lines = []
    for cert in certifications:
        name = _get(cert, "Name")
        if not name:
            continue
        issued = _get(cert, "Started On")
        expires = _get(cert, "Finished On")
        detail = [_get(cert, "Authority")]
        detail.append(f"issued {issued}" if issued else "")
        detail.append(f"expires {expires}" if expires else "")
        detail = [d for d in detail if d]
        lines.append(f"- {name}" + (f" — {', '.join(detail)}" if detail else ""))
    return lines


def _render_recommendations(recommendations: list[dict]) -> list[str]:
    lines = []
    for rec in recommendations:
        author = " ".join(
            p for p in (_get(rec, "First Name"), _get(rec, "Last Name")) if p
        )
        role = ", ".join(
            p for p in (_get(rec, "Job Title"), _get(rec, "Company")) if p
        )
        who = author or "an unnamed recommender"
        lines.append(f"\n### Written by {who}" + (f" ({role})" if role else ""))
        text = _get(rec, "Text")
        if text:
            lines.append(text)
    return lines

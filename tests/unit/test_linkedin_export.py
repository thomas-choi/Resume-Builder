"""LinkedIn data-export reader — deterministic parsing, no network, no scraping."""

import pytest

from src.tools.linkedin_export import read_linkedin_export
from tests.conftest import LINKEDIN_CSVS, build_linkedin_export_zip


def test_reads_every_section_of_the_export(sample_linkedin_zip):
    doc = read_linkedin_export(sample_linkedin_zip)

    assert doc.id == "linkedin:Basic_LinkedInDataExport.zip"
    assert doc.source_type == "linkedin"
    assert set(doc.structured_fields) == {
        "profile",
        "positions",
        "education",
        "skills",
        "certifications",
        "recommendations_received",
    }
    assert doc.structured_fields["profile"]["Headline"] == "Senior Engineer"
    assert len(doc.structured_fields["positions"]) == 2
    assert [s["Name"] for s in doc.structured_fields["skills"]] == [
        "Python",
        "PostgreSQL",
    ]


def test_structured_rows_drop_absent_fields(sample_linkedin_zip):
    # An empty cell must stay absent rather than becoming "" — downstream the
    # extractor is told never to invent what a source does not state.
    doc = read_linkedin_export(sample_linkedin_zip)
    current, past = doc.structured_fields["positions"]
    assert "Finished On" not in current  # still in the role
    assert past["Finished On"] == "Dec 2020"


def test_notes_preamble_before_the_header_is_skipped(sample_linkedin_zip):
    doc = read_linkedin_export(sample_linkedin_zip)
    positions = doc.structured_fields["positions"]
    assert [p["Company Name"] for p in positions] == ["Acme Corp", "Globex"]
    assert all("Notes:" not in key for p in positions for key in p)


def test_rendered_text_carries_labelled_sections(sample_linkedin_zip):
    raw = read_linkedin_export(sample_linkedin_zip).raw_text

    assert "## Positions" in raw
    assert "### Senior Engineer — Acme Corp" in raw
    assert "Dates: Jan 2021 – Present" in raw  # open end = current role
    assert "Dates: Mar 2018 – Dec 2020" in raw
    assert "## Education" in raw and "Degree: BSc Computer Science" in raw
    assert "- Python" in raw
    # A certification with no end date has no expiry — not an ongoing range.
    assert "- AWS Solutions Architect — Amazon Web Services, issued Feb 2022" in raw
    assert "AWS Solutions Architect — Amazon Web Services, issued Feb 2022, expires" not in raw
    # Recommendations are attributed to their author, never to the person.
    assert "## Recommendations received (written by other people)" in raw
    assert "### Written by Bob Jones (CTO, Acme Corp)" in raw


def test_reads_a_single_csv_from_the_export(tmp_path):
    path = tmp_path / "Positions.csv"
    path.write_text(LINKEDIN_CSVS["Positions.csv"])

    doc = read_linkedin_export(path)

    assert doc.id == "linkedin:Positions.csv"
    assert set(doc.structured_fields) == {"positions"}
    assert "### Engineer — Globex" in doc.raw_text


def test_filename_variants_map_to_the_same_section(tmp_path):
    path = tmp_path / "export.zip"
    path.write_bytes(
        build_linkedin_export_zip(
            {"Recommendations Received.csv": LINKEDIN_CSVS["Recommendations_Received.csv"]}
        )
    )

    doc = read_linkedin_export(path)

    assert len(doc.structured_fields["recommendations_received"]) == 1


def test_unrecognized_files_are_ignored(tmp_path):
    path = tmp_path / "export.zip"
    path.write_bytes(
        build_linkedin_export_zip(
            {
                "Skills.csv": LINKEDIN_CSVS["Skills.csv"],
                "Ad_Targeting.csv": "Member Age,Language\r\n25-34,English\r\n",
            }
        )
    )

    doc = read_linkedin_export(path)

    assert set(doc.structured_fields) == {"skills"}


def test_export_without_any_known_section_raises(tmp_path):
    path = tmp_path / "export.zip"
    path.write_bytes(build_linkedin_export_zip({"Ad_Targeting.csv": "Member Age\r\n25\r\n"}))

    with pytest.raises(ValueError, match="no recognized LinkedIn export sections"):
        read_linkedin_export(path)


def test_unsupported_file_type_raises(tmp_path):
    path = tmp_path / "profile.pdf"
    path.write_bytes(b"%PDF-1.4")

    with pytest.raises(ValueError, match="unsupported LinkedIn export file type"):
        read_linkedin_export(path)


def test_corrupt_zip_raises(tmp_path):
    path = tmp_path / "export.zip"
    path.write_bytes(b"not a zip at all")

    with pytest.raises(ValueError, match="not a readable ZIP archive"):
        read_linkedin_export(path)

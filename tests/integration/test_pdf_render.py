"""PDF conversion against a real LibreOffice install (present in the image).

Run with: pytest -m integration
Requires a *working* `soffice` (`LIBREOFFICE_BIN`) — the docker image ships one;
a host without it (or with a broken install) skips, since the unit suite already
covers the docx rendering and the degrade-without-LibreOffice path.
"""

import shutil
import subprocess

import pytest

from src import config
from src.models.schemas import CoverLetter, Experience, TailoredCV
from src.tools import docx_renderer


def _libreoffice_works() -> bool:
    """Probe the binary: being on PATH is not proof it can run."""
    if shutil.which(config.LIBREOFFICE_BIN) is None:
        return False
    try:
        return (
            subprocess.run(
                [config.LIBREOFFICE_BIN, "--version"], capture_output=True, timeout=60
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _libreoffice_works(),
        reason=f"no working {config.LIBREOFFICE_BIN} on this host",
    ),
]


def test_cv_docx_converts_to_pdf(tmp_path):
    cv = TailoredCV(
        headline="Senior Backend Engineer",
        summary="Eight years building data-heavy backends.",
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
    docx = docx_renderer.render_cv(cv, tmp_path / "cv.docx", name="Alice Smith")

    pdf = docx_renderer.convert_to_pdf(docx)

    assert pdf is not None and pdf.exists()
    assert pdf.read_bytes().startswith(b"%PDF")


def test_cover_letter_docx_converts_to_pdf(tmp_path):
    letter = CoverLetter(
        greeting="Dear Hiring Manager,",
        body_paragraphs=["I would like to apply for the backend engineer role."],
        closing="Sincerely,",
    )
    docx = docx_renderer.render_cover_letter(
        letter, tmp_path / "cover-letter.docx", name="Alice Smith"
    )

    pdf = docx_renderer.convert_to_pdf(docx)

    assert pdf is not None and pdf.exists()
    assert pdf.read_bytes().startswith(b"%PDF")

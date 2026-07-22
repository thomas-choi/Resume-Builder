"""End-to-end pipeline against the real Anthropic API.

Run with: pytest -m integration
Requires ANTHROPIC_API_KEY; skipped otherwise.
"""

import os

import pytest

from src.agents.ingestion_graph import build_ingestion_graph
from src.agents.tailoring_graph import build_tailoring_graph
from src.tools.docx_reader import read_docx

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
    ),
]

JOB_POST = """
Backend Engineer — TradeTech Ltd
We are looking for a backend engineer with strong Python experience to build
distributed systems for financial data processing. PostgreSQL experience is a
plus. You will own services end to end.
"""


def test_full_pipeline_on_sample_cv(sample_docx, data_dir):
    source = read_docx(sample_docx)

    ingestion = build_ingestion_graph()
    state = ingestion.invoke({"sources": [source]})
    profile = state["profile"]

    assert profile.name
    assert profile.experiences, "expected at least one experience from the sample CV"
    assert profile.raw_source_map, "raw_source_map must be populated"

    tailoring = build_tailoring_graph()
    # The Phase 4 graph carries a checkpointer, so every run needs a thread id
    # (the API uses the tailor_id). Nothing is rendered here, so the human-review
    # node passes straight through and the run cannot pause.
    tstate = tailoring.invoke(
        {"profile": profile, "job_post": JOB_POST},
        {"configurable": {"thread_id": "integration-pipeline"}},
    )

    cv = tstate["tailored_cv"]
    validation = tstate["validation"]
    assert cv.summary
    # every highlighted skill must exist in the profile (anti-fabrication)
    profile_skills = {s.name.lower() for s in profile.skills}
    unsourced = [s for s in cv.highlighted_skills if s.lower() not in profile_skills]
    assert not unsourced or validation.needs_review

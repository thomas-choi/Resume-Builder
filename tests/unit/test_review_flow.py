"""Phase 4 human-in-the-loop: the interrupt, the decision, and what resumes.

The invariant under test is design doc §11: a CV whose claims the gate could
not trace is never rendered without a person seeing those claims — and when
they reject one, only that claim disappears.
"""

import pytest

from src import config
from src.agents import job_analysis, review, tailoring, tailoring_graph, validation
from src.models.schemas import (
    CareerProfile,
    CoverLetter,
    Experience,
    JobRequirements,
    Project,
    ReviewDecision,
    ReviewRequest,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)


def _flagged_cv() -> TailoredCV:
    return TailoredCV(
        headline="Senior Backend Engineer",
        summary="A concise pitch.",
        selected_experiences=[
            Experience(
                company="Acme Corp",
                title="Senior Engineer",
                bullets=[
                    "Built a distributed trading backtester in Python",
                    "Ran a team of 40 engineers",
                ],
                source="cv_docx:resume.docx",
            )
        ],
        selected_projects=[
            Project(name="backtester", description="engine", source="github:alice")
        ],
        highlighted_skills=["Python", "Kubernetes"],
    )


def _flagged_validation() -> ValidationResult:
    return ValidationResult(
        passed=False,
        needs_review=True,
        flags=[
            ValidationFlag(
                item="Ran a team of 40 engineers",
                kind="bullet",
                reason="No profile bullet mentions managing a team",
                similarity=0.21,
            ),
            ValidationFlag(
                item="Kubernetes",
                kind="skill",
                reason="Skill not present in the career profile",
            ),
        ],
    )


def _mock_nodes(monkeypatch, cv=None, result=None):
    """Mock every LLM-backed node in the tailoring graph, review agent included."""
    monkeypatch.setattr(
        job_analysis, "analyze", lambda job_post: JobRequirements(title="Backend Engineer")
    )
    monkeypatch.setattr(tailoring, "tailor", lambda p, r: cv or _flagged_cv())
    monkeypatch.setattr(
        validation, "validate", lambda p, c: result or _flagged_validation()
    )
    monkeypatch.setattr(
        tailoring,
        "generate_cover_letter",
        lambda p, r, c: CoverLetter(greeting="Dear Hiring Manager,", closing="Sincerely,"),
    )
    monkeypatch.setattr(review, "write_brief", lambda *a, **k: "A brief for the human.")
    monkeypatch.setattr(config, "RENDER_PDF", False)  # no LibreOffice in unit tests


def _thread(tailor_id: str) -> dict:
    return {"configurable": {"thread_id": tailor_id}}


def _start(sample_profile, tailor_id: str, **overrides) -> tuple[object, dict]:
    graph = tailoring_graph.build_tailoring_graph()
    state = graph.invoke(
        {
            "profile": sample_profile,
            "job_post": "A job post",
            "tailor_id": tailor_id,
            "render": True,
            **overrides,
        },
        _thread(tailor_id),
    )
    return graph, state


# --------------------------------------------------------------------------
# build_review_request / apply_decision (no graph)
# --------------------------------------------------------------------------


def test_review_request_carries_ids_and_the_closest_profile_claim(sample_profile):
    request = review.build_review_request(
        "t-1", sample_profile, _flagged_validation(), brief="hello"
    )
    assert [i.id for i in request.items] == ["flag-0", "flag-1"]
    assert request.brief == "hello"
    bullet = request.items[0]
    assert bullet.kind == "bullet"
    # The reviewer is shown the nearest sourced claim, with its source doc id.
    assert bullet.closest_profile_text in [
        b for e in sample_profile.experiences for b in e.bullets
    ]
    assert bullet.source == "cv_docx:resume.docx"


def test_rejected_items_are_removed_from_the_cv(sample_profile):
    request = review.build_review_request("t-1", sample_profile, _flagged_validation())
    cv, result = review.apply_decision(
        _flagged_cv(), request, ReviewDecision(approvals={"flag-0": False, "flag-1": False})
    )
    assert cv.selected_experiences[0].bullets == [
        "Built a distributed trading backtester in Python"
    ]
    assert cv.highlighted_skills == ["Python"]
    # Nothing unsupported survived, so the run is clean and needs no more review.
    assert result.passed is True
    assert result.flags == []
    assert result.needs_review is False


def test_approved_items_stay_and_are_recorded(sample_profile):
    request = review.build_review_request("t-1", sample_profile, _flagged_validation())
    cv, result = review.apply_decision(
        _flagged_cv(), request, ReviewDecision(approvals={"flag-0": True, "flag-1": False})
    )
    assert "Ran a team of 40 engineers" in cv.selected_experiences[0].bullets
    assert cv.highlighted_skills == ["Python"]
    # A human accepting a claim is not the gate having traced it: keep the flag.
    assert result.passed is False
    assert [f.item for f in result.flags] == ["Ran a team of 40 engineers"]
    assert result.needs_review is False


def test_unanswered_items_are_removed_not_kept(sample_profile):
    # Silence is not consent for a claim that could not be traced.
    request = review.build_review_request("t-1", sample_profile, _flagged_validation())
    cv, _ = review.apply_decision(_flagged_cv(), request, ReviewDecision())
    assert cv.selected_experiences[0].bullets == [
        "Built a distributed trading backtester in Python"
    ]
    assert cv.highlighted_skills == ["Python"]


def test_approve_all_keeps_everything(sample_profile):
    request = review.build_review_request("t-1", sample_profile, _flagged_validation())
    cv, result = review.apply_decision(
        _flagged_cv(), request, ReviewDecision(approve_all=True)
    )
    assert len(cv.selected_experiences[0].bullets) == 2
    assert cv.highlighted_skills == ["Python", "Kubernetes"]
    assert len(result.flags) == 2


def test_a_rejected_claim_is_also_removed_from_the_prose(sample_profile):
    # Found in a live run: dropping "Docker" from highlighted_skills left the
    # summary still calling the person "proficient in ... Docker".
    cv = _flagged_cv().model_copy(
        update={
            "headline": "Kubernetes Platform Engineer",
            "summary": (
                "Senior engineer with eight years in Python. "
                "Proficient in Python, PostgreSQL and Kubernetes. "
                "Led database migrations end to end."
            ),
        }
    )
    request = review.build_review_request("t-1", sample_profile, _flagged_validation())
    pruned, _ = review.apply_decision(cv, request, ReviewDecision(), sample_profile)

    assert "Kubernetes" not in pruned.summary
    assert "Senior engineer with eight years in Python." in pruned.summary
    assert "Led database migrations end to end." in pruned.summary
    # A headline naming a rejected claim falls back to the profile's own.
    assert pruned.headline == sample_profile.headline


def test_prose_survives_when_nothing_is_rejected(sample_profile):
    cv = _flagged_cv().model_copy(
        update={"headline": "Kubernetes Engineer", "summary": "Proficient in Kubernetes."}
    )
    request = review.build_review_request("t-1", sample_profile, _flagged_validation())
    pruned, _ = review.apply_decision(
        cv, request, ReviewDecision(approve_all=True), sample_profile
    )
    assert pruned.summary == "Proficient in Kubernetes."
    assert pruned.headline == "Kubernetes Engineer"


def test_prose_scrub_matches_whole_words_only(sample_profile):
    # "Go" must not gut a sentence about "Google" or "goal".
    validation_result = ValidationResult(
        passed=False,
        needs_review=True,
        flags=[ValidationFlag(item="Go", kind="skill", reason="not in profile")],
    )
    cv = _flagged_cv().model_copy(
        update={"summary": "Worked at Google on a goal-tracking service. Wrote Go daily."}
    )
    request = review.build_review_request("t-1", sample_profile, validation_result)
    pruned, _ = review.apply_decision(cv, request, ReviewDecision(), sample_profile)
    assert pruned.summary == "Worked at Google on a goal-tracking service."


def test_rejecting_an_experience_scrubs_the_employer_from_the_prose(sample_profile):
    validation_result = ValidationResult(
        passed=False,
        needs_review=True,
        flags=[
            ValidationFlag(
                item="Principal Engineer at Globex", kind="experience", reason="unknown"
            )
        ],
    )
    cv = _flagged_cv().model_copy(
        update={"summary": "Ten years in Python. Principal engineer at Globex since 2019."}
    )
    request = review.build_review_request("t-1", sample_profile, validation_result)
    pruned, _ = review.apply_decision(cv, request, ReviewDecision(), sample_profile)
    assert pruned.summary == "Ten years in Python."


def test_rejecting_a_whole_experience_or_project_drops_it(sample_profile):
    validation_result = ValidationResult(
        passed=False,
        needs_review=True,
        flags=[
            ValidationFlag(
                item="Senior Engineer at Acme Corp", kind="experience", reason="unknown"
            ),
            ValidationFlag(item="backtester", kind="project", reason="unknown"),
        ],
    )
    request = review.build_review_request("t-1", sample_profile, validation_result)
    cv, _ = review.apply_decision(_flagged_cv(), request, ReviewDecision())
    assert cv.selected_experiences == []
    assert cv.selected_projects == []


# --------------------------------------------------------------------------
# The graph: interrupt, resume, and the paths that never pause
# --------------------------------------------------------------------------


def test_flagged_run_interrupts_before_rendering(monkeypatch, data_dir, sample_profile):
    _mock_nodes(monkeypatch)
    graph, state = _start(sample_profile, "t-pause")

    payload = state["__interrupt__"][0].value
    assert payload["tailor_id"] == "t-pause"
    assert [i["id"] for i in payload["items"]] == ["flag-0", "flag-1"]
    assert payload["brief"] == "A brief for the human."
    # Paused, not finished — and nothing on disk.
    assert graph.get_state(_thread("t-pause")).next == ("human_review",)
    assert not (data_dir / "documents" / "t-pause" / "cv.docx").exists()


def test_pending_review_is_persisted_for_the_api(monkeypatch, data_dir, sample_profile):
    from src.utils import document_store

    _mock_nodes(monkeypatch)
    _start(sample_profile, "t-persist")
    stored = document_store.load_review("t-persist")
    assert stored["tailor_id"] == "t-persist"
    assert len(stored["items"]) == 2
    assert ReviewRequest.model_validate(
        {k: v for k, v in stored.items() if k != "created_at"}
    )


def test_resume_renders_the_reviewed_cv_without_rejected_claims(
    monkeypatch, data_dir, sample_profile
):
    from docx import Document

    _mock_nodes(monkeypatch)
    graph, _ = _start(sample_profile, "t-resume")

    from langgraph.types import Command

    state = graph.invoke(
        Command(resume=ReviewDecision(approvals={"flag-0": False, "flag-1": True}).model_dump()),
        _thread("t-resume"),
    )

    assert "__interrupt__" not in state
    assert [d["kind"] for d in state["documents"]] == ["cv"]
    assert state["render_skipped"] is None
    assert state["validation"].needs_review is False
    # The rejected bullet is gone from the rendered document, the approved
    # skill is still there.
    text = "\n".join(
        p.text for p in Document(str(data_dir / "documents" / "t-resume" / "cv.docx")).paragraphs
    )
    assert "Ran a team of 40" not in text
    assert "Kubernetes" in text


def test_resuming_does_not_rewrite_the_brief(monkeypatch, data_dir, sample_profile):
    # LangGraph re-runs an interrupted node from the top on resume, so the
    # brief's LLM call lives in prepare_review — a completed node — and must
    # not fire a second time when the decision arrives.
    from langgraph.types import Command

    _mock_nodes(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(
        review, "write_brief", lambda *a, **k: calls.append(1) or "A brief."
    )

    graph, _ = _start(sample_profile, "t-once")
    assert len(calls) == 1
    graph.invoke(Command(resume=ReviewDecision().model_dump()), _thread("t-once"))
    assert len(calls) == 1


def test_cover_letter_is_written_after_review(monkeypatch, data_dir, sample_profile):
    # The letter must draw only on claims that survived review, so it is
    # generated on the resumed run, not before the pause.
    seen: list[TailoredCV] = []
    _mock_nodes(monkeypatch)
    monkeypatch.setattr(
        tailoring,
        "generate_cover_letter",
        lambda p, r, c: seen.append(c)
        or CoverLetter(greeting="Dear Hiring Manager,", closing="Sincerely,"),
    )
    graph, _ = _start(sample_profile, "t-letter", want_cover_letter=True)
    assert seen == []  # not written before the human answered

    from langgraph.types import Command

    state = graph.invoke(Command(resume=ReviewDecision().model_dump()), _thread("t-letter"))
    assert state["cover_letter"].greeting == "Dear Hiring Manager,"
    assert "Ran a team of 40 engineers" not in seen[0].selected_experiences[0].bullets


def test_clean_run_never_pauses(monkeypatch, data_dir, sample_profile):
    _mock_nodes(monkeypatch, result=ValidationResult(passed=True))
    graph, state = _start(sample_profile, "t-clean")
    assert "__interrupt__" not in state
    assert [d["kind"] for d in state["documents"]] == ["cv"]
    assert graph.get_state(_thread("t-clean")).next == ()


def test_run_without_rendering_never_pauses(monkeypatch, data_dir, sample_profile):
    # Nothing can be rendered, so there is nothing to gate: a caller inspecting
    # flags via the API is not made to answer a review first.
    _mock_nodes(monkeypatch)
    graph, state = _start(sample_profile, "t-noreview", render=False)
    assert "__interrupt__" not in state
    assert state["render_skipped"] == "rendering not requested"
    assert state["validation"].needs_review is True


def test_review_survives_a_missing_brief(monkeypatch, data_dir, sample_profile):
    # The brief is a convenience; losing it must not block the human gate.
    _mock_nodes(monkeypatch)
    monkeypatch.setattr(review, "write_brief", lambda *a, **k: "")
    _, state = _start(sample_profile, "t-nobrief")
    assert state["__interrupt__"][0].value["brief"] == ""
    assert len(state["__interrupt__"][0].value["items"]) == 2


def test_a_default_decision_removes_every_flagged_claim(
    monkeypatch, data_dir, sample_profile
):
    # What the API sends when a reviewer approves nothing: the run completes,
    # having dropped every claim the gate could not trace.
    from langgraph.types import Command

    _mock_nodes(monkeypatch)
    graph, _ = _start(sample_profile, "t-default")
    state = graph.invoke(
        Command(resume=ReviewDecision().model_dump()), _thread("t-default")
    )
    assert state["tailored_cv"].highlighted_skills == ["Python"]
    assert state["validation"].passed is True
    assert state["documents"]


def test_profile_without_experiences_still_builds_a_request():
    # _closest_profile_claim has nothing to compare against; the item must
    # still reach the human rather than raising.
    request = review.build_review_request(
        "t-1", CareerProfile(name="Alice"), _flagged_validation()
    )
    assert request.items[0].closest_profile_text is None
    assert request.items[0].source is None

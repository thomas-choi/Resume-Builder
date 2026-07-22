"""Extraction agent with mocked LLM: source ids are enforced in code."""

import logging

import httpx
import pytest
from pydantic import ValidationError

from src import config
from src.agents import extraction
from src.models.schemas import (
    Experience,
    Project,
    Skill,
    SourceDocument,
    SourceExtraction,
)
from src.tools.github_client import API_BASE, fetch_github_profile
from tests.conftest import FakeLLM, RawMessage


def _doc() -> SourceDocument:
    return SourceDocument(
        id="cv_docx:resume.docx", source_type="cv_docx", raw_text="Alice Smith ..."
    )


def test_extract_one_overwrites_source(monkeypatch):
    # The LLM "forgets" to set the right source; code must overwrite it.
    llm_output = SourceExtraction(
        name="Alice Smith",
        experiences=[
            Experience(company="Acme Corp", title="Senior Engineer", source="WRONG")
        ],
        projects=[Project(name="backtester", description="engine", source="WRONG")],
    )
    fake = FakeLLM(llm_output)
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    doc = SourceDocument(
        id="cv_docx:resume.docx", source_type="cv_docx", raw_text="Alice Smith ..."
    )
    result = extraction.extract_one(doc).extraction

    assert result.experiences[0].source == "cv_docx:resume.docx"
    assert result.projects[0].source == "cv_docx:resume.docx"
    # Prompt contained the document text and source id
    system, user = fake.calls[0][0], fake.calls[0][1]
    assert "cv_docx:resume.docx" in system[1]
    assert "Alice Smith ..." in user[1]


def test_source_extraction_skill_body_in_system_prompt(monkeypatch):
    fake = FakeLLM(SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)
    extraction.extract_one(_doc())
    system_prompt = fake.calls[0][0][1]
    # The migrated source-extraction skill body is composed into the prompt.
    assert "You extract structured career data from one raw source document." in system_prompt
    assert "Fact vs. inference" in system_prompt
    # Runtime source-id scaffolding survives alongside the skill.
    assert "cv_docx:resume.docx" in system_prompt


def test_structured_fields_are_sent_as_authoritative(monkeypatch):
    # Phase 2: a LinkedIn export carries exported records alongside the
    # rendered text; the records must reach the model marked as authoritative.
    fake = FakeLLM(SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    extraction.extract_one(
        SourceDocument(
            id="linkedin:export.zip",
            source_type="linkedin",
            raw_text="# LinkedIn data export\n\n## Positions\n### Engineer — Acme",
            structured_fields={"positions": [{"Company Name": "Acme Corp"}]},
        )
    )

    system_prompt, user_prompt = fake.calls[0][0][1], fake.calls[0][1][1]
    assert "STRUCTURED FIELDS (JSON)" in user_prompt
    assert '"Company Name": "Acme Corp"' in user_prompt
    assert "prefer them over the rendered text" in user_prompt
    # The rendered text is still there — the records win, they don't replace it.
    assert "### Engineer — Acme" in user_prompt
    # The skill's export reasoning (self-asserted skills, third-party
    # recommendations) is in the system prompt.
    assert "Structured exports (LinkedIn and similar)" in system_prompt


def test_prose_source_gets_no_structured_block(monkeypatch):
    fake = FakeLLM(SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    extraction.extract_one(_doc())

    assert "STRUCTURED FIELDS" not in fake.calls[0][1][1]


def _github_payload() -> dict:
    """30 repos as the GitHub extractor emits them — 3 with no description."""
    projects = [
        {
            "name": f"repo-{i}",
            "description": None if i in (11, 13, 27) else f"description {i}",
            "technologies": ["Python"],
            "source": "github:thomas-choi",
        }
        for i in range(30)
    ]
    return {"name": "Thomas Choi", "projects": projects}


def _failed_envelope(args: dict, error: Exception | None = None) -> dict:
    """The envelope LangChain returns when structured output fails to validate."""
    if error is None:
        try:
            SourceExtraction.model_validate(args)
        except ValidationError as exc:
            error = exc
    return {"parsed": None, "raw": RawMessage(args), "parsing_error": error}


def test_extract_one_keeps_projects_with_null_description(monkeypatch):
    # Regression: three description-less repos used to reject all 30 projects
    # (and both parsed CVs alongside them) with a `string_type` error.
    payload = _github_payload()
    fake = FakeLLM(lambda messages: SourceExtraction.model_validate(payload))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    result = extraction.extract_one(
        SourceDocument(
            id="github:thomas-choi", source_type="github", raw_text="repos ..."
        )
    ).extraction

    assert len(result.projects) == 30
    assert [p.description for p in result.projects[11:14:2]] == ["", ""]
    assert result.projects[27].description == ""
    assert result.projects[27].name == "repo-27"
    # The source id is still enforced in code on the salvaged-through path.
    assert {p.source for p in result.projects} == {"github:thomas-choi"}


def test_extract_one_drops_only_the_malformed_item(monkeypatch, caplog):
    args = {
        "name": "Alice Smith",
        "projects": [
            {"name": "good-1", "description": "fine", "source": "x"},
            {"name": "bad", "technologies": {"not": "a list"}, "source": "x"},
            {"name": "good-2", "description": "fine", "source": "x"},
        ],
    }
    fake = FakeLLM(_failed_envelope(args))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    with caplog.at_level(logging.WARNING):
        result = extraction.extract_one(_doc()).extraction

    assert [p.name for p in result.projects] == ["good-1", "good-2"]
    assert result.name == "Alice Smith"
    # The drop is logged with the list index and the item's name.
    assert "projects[1]" in caplog.text and "'bad'" in caplog.text


def test_extract_one_reraises_when_nothing_is_salvageable(monkeypatch):
    args = {
        "projects": [
            {"name": "bad-1", "technologies": {"not": "a list"}, "source": "x"},
            {"name": "bad-2", "technologies": {"not": "a list"}, "source": "x"},
        ]
    }
    fake = FakeLLM(_failed_envelope(args))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    # An empty profile is worse than a 500 — the original error must surface.
    with pytest.raises(ValidationError):
        extraction.extract_one(_doc())


def test_extract_one_reraises_without_a_tool_call(monkeypatch):
    error = ValueError("no tool call")
    fake = FakeLLM({"parsed": None, "raw": RawMessage(None), "parsing_error": error})
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    with pytest.raises(ValueError, match="no tool call"):
        extraction.extract_one(_doc())


def test_extract_one_requests_raw_output(monkeypatch):
    fake = FakeLLM(SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)
    extraction.extract_one(_doc())
    assert fake.include_raw is True


def test_extraction_degrades_without_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path / "absent")
    fake = FakeLLM(SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)
    # Call still works with the skill absent (graceful degradation).
    result = extraction.extract_one(_doc()).extraction
    assert result.name == "Alice Smith"
    system_prompt = fake.calls[0][0][1]
    assert "Fact vs. inference" not in system_prompt
    # Runtime scaffolding is unaffected by the missing skill.
    assert "cv_docx:resume.docx" in system_prompt


def _github_doc_with_external_section() -> SourceDocument:
    """A real GitHub source document containing an external-contribution section."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/alice/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "backtester",
                        "full_name": "alice/backtester",
                        "owner": {"login": "alice"},
                        "description": "Backtesting engine",
                        "fork": False,
                    }
                ],
            )
        if path == "/search/issues":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "title": "Fix request context teardown",
                            "repository_url": f"{API_BASE}/repos/pallets/flask",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=API_BASE)
    return fetch_github_profile("alice", client=client)


def _github_doc_with_repos(count: int) -> SourceDocument:
    """A real rendered GitHub document owning `count` repos."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/users/alice/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": f"repo-{i}",
                        "full_name": f"alice/repo-{i}",
                        "owner": {"login": "alice"},
                        "description": f"description {i}",
                        "fork": False,
                    }
                    for i in range(count)
                ],
            )
        return httpx.Response(404, json={"message": "not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=API_BASE)
    return fetch_github_profile("alice", client=client)


def _user_prompts(fake: FakeLLM) -> list[str]:
    return [call[1][1] for call in fake.calls]


def test_github_document_is_extracted_in_batches(monkeypatch):
    # One document holding every repo is what asked for more structured output
    # than the model reliably returns; repos go out a batch at a time instead.
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", False)
    monkeypatch.setattr(config, "GITHUB_REPOS_PER_EXTRACTION", 4)
    fake = FakeLLM(lambda messages: SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    extraction.extract_one(_github_doc_with_repos(10))

    prompts = _user_prompts(fake)
    assert len(prompts) == 3  # 4 + 4 + 2
    assert [p.count("### Repository:") for p in prompts] == [4, 4, 2]
    # Every batch carries the tier heading that decides attribution, and the
    # repos are partitioned across batches rather than duplicated.
    assert all("## Owned repositories" in p for p in prompts)
    seen = [f"alice/repo-{i}" for i in range(10)]
    assert sorted(name for p in prompts for name in seen if name + "\n" in p) == sorted(seen)


def test_single_batch_github_matches_a_plain_single_call(monkeypatch):
    """Below the batch size, batching must not change what the model is asked."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", False)
    monkeypatch.setattr(config, "GITHUB_REPOS_PER_EXTRACTION", 10)
    doc = _github_doc_with_repos(3)

    fake = FakeLLM(lambda messages: SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)
    result = extraction.extract_one(doc)

    assert len(fake.calls) == 1
    # The one batch re-renders the document byte-for-byte.
    assert doc.raw_text in _user_prompts(fake)[0]
    assert result.errors == []
    assert result.pruned_text is None


def test_failed_batch_isolates_to_the_offending_repo(monkeypatch, caplog):
    """The failure this phase exists for: blame one repo, keep the rest."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", False)
    monkeypatch.setattr(config, "GITHUB_REPOS_PER_EXTRACTION", 3)

    def respond(messages):
        prompt = messages[1][1]
        # repo-4 poisons any call it appears in — its batch, then itself.
        if "alice/repo-4\n" in prompt:
            raise ValueError("no tool call returned")
        names = [f"alice/repo-{i}" for i in range(6) if f"alice/repo-{i}\n" in prompt]
        return SourceExtraction(
            name="Alice Smith",
            projects=[
                Project(name=n, description="d", source="WRONG") for n in names
            ],
        )

    fake = FakeLLM(respond)
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    with caplog.at_level(logging.WARNING):
        result = extraction.extract_one(_github_doc_with_repos(6))

    # Five of six repos survive; only repo-4 is lost.
    assert [p.name for p in result.extraction.projects] == [
        "alice/repo-0",
        "alice/repo-1",
        "alice/repo-2",
        "alice/repo-3",
        "alice/repo-5",
    ]
    assert result.errors == [
        {
            "source": "github:alice",
            "repo": "alice/repo-4",
            "reason": "no tool call returned",
        }
    ]
    # Traceability still enforced in code across the merged batches.
    assert {p.source for p in result.extraction.projects} == {"github:alice"}
    # The archive is rewritten without the failed repo, and only that one.
    assert "alice/repo-4" not in result.pruned_text
    assert "alice/repo-5" in result.pruned_text
    assert "## Owned repositories" in result.pruned_text
    assert "retrying its 3 repos one at a time" in caplog.text


def test_github_extraction_raises_only_when_every_repo_fails(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", False)
    monkeypatch.setattr(config, "GITHUB_REPOS_PER_EXTRACTION", 2)

    def dead(messages):
        raise ValueError("no tool call returned")

    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: FakeLLM(dead))

    # A silently empty GitHub source is worse than a failed one.
    with pytest.raises(ValueError, match="all 4 repositories"):
        extraction.extract_one(_github_doc_with_repos(4))


def test_merge_keeps_first_identity_and_concatenates_lists(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", False)
    monkeypatch.setattr(config, "GITHUB_REPOS_PER_EXTRACTION", 1)

    batches = [
        SourceExtraction(name="Alice Smith", skills=[Skill(name="Python", category="language")]),
        SourceExtraction(name="A. Smith", headline="Engineer", skills=[Skill(name="Go", category="language")]),
    ]
    fake = FakeLLM(list(batches))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    result = extraction.extract_one(_github_doc_with_repos(2))

    assert result.extraction.name == "Alice Smith"  # first non-empty wins
    assert result.extraction.headline == "Engineer"  # filled in by a later batch
    assert [s.name for s in result.extraction.skills] == ["Python", "Go"]


def test_no_tool_call_logs_real_diagnostics(monkeypatch, caplog):
    """The old log said literally `: None` — say what actually happened."""

    class Bare:
        tool_calls = []
        content = "I cannot comply with this request."
        response_metadata = {"finish_reason": "length"}
        usage_metadata = {"output_tokens": 16384}

    fake = FakeLLM({"parsed": None, "raw": Bare(), "parsing_error": None})
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    with caplog.at_level(logging.WARNING), pytest.raises(ValueError):
        extraction.extract_one(_doc())

    assert "finish_reason='length'" in caplog.text
    assert "output_tokens" in caplog.text
    assert "I cannot comply" in caplog.text
    assert "tool_calls=0" in caplog.text


def test_external_repo_section_carries_not_owned_framing(monkeypatch):
    # Anti-fabrication: a contribution to someone else's repo must reach the
    # model labelled as a contribution, never as one of the user's projects.
    monkeypatch.setattr(config, "GITHUB_TOKEN", None)
    monkeypatch.setattr(config, "GITHUB_INCLUDE_CONTRIBUTIONS", True)
    fake = FakeLLM(SourceExtraction(name="Alice Smith"))
    monkeypatch.setattr(extraction, "make_llm", lambda model, **kw: fake)

    extraction.extract_one(_github_doc_with_external_section())

    system_prompt, user_prompt = fake.calls[0][0][1], fake.calls[0][1][1]
    # The source-extraction skill's ownership rule is in the system prompt.
    assert "Ownership vs. contribution" in system_prompt
    assert "authorship or ownership of the project" in system_prompt
    # The document itself labels the external section and the owned one apart.
    assert "## Owned repositories" in user_prompt
    assert (
        "## Contributions to external repositories (not owned by the user)"
        in user_prompt
    )
    assert "pallets/flask (owned by others)" in user_prompt
    assert "Contribution scope: 1 merged pull request" in user_prompt

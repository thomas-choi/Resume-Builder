"""ReviewAgent — the first AgentBase-derived, tool-calling node (Phase 1.b deferral).

What matters here is the machinery the rest of the pipeline does not use: the
`skills/` directory loaded through `AgentBase._load_skills`, and FUND's runtime
`load_skill_from_fs` tool registered via `register_tool` so the agent can pull a
skill body mid-loop instead of being handed one.
"""

from langchain_core.messages import AIMessage

from src import config
from src.agents import review
from src.models.schemas import JobRequirements, ReviewItem, ReviewRequest


class ScriptedLLM:
    """Chat model stub for the tool loop: returns queued messages in order."""

    def __init__(self, messages):
        self.queue = list(messages)
        self.bound_tools = None
        self.conversations: list = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.conversations.append(list(messages))
        return self.queue.pop(0)


def _request() -> ReviewRequest:
    return ReviewRequest(
        tailor_id="t-1",
        items=[
            ReviewItem(
                id="flag-0",
                item="Ran a team of 40 engineers",
                kind="bullet",
                reason="No profile bullet mentions managing a team",
                similarity=0.21,
            )
        ],
    )


def test_agent_loads_the_shipped_skills():
    agent = review.ReviewAgent()
    names = {s["name"] for s in agent._skill_registry}
    assert {"cv-review", "anti-fabrication", "cv-tailoring"} <= names
    context = agent.get_skills_context()
    assert "cv-review" in context and "anti-fabrication" in context


def test_agent_registers_the_runtime_load_skill_tool():
    agent = review.ReviewAgent()
    assert [t.name for t in agent.tools] == ["load_skill_from_fs"]
    body = agent.tools[0].invoke({"skill_name": "anti-fabrication"})
    assert "strict fact-checker" in body
    assert "---" not in body.split("\n")[0]  # frontmatter stripped


def test_unknown_skill_is_reported_not_raised():
    agent = review.ReviewAgent()
    assert "not found" in agent.tools[0].invoke({"skill_name": "nope"})


def test_write_brief_runs_the_tool_loop(monkeypatch):
    llm = ScriptedLLM(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill_from_fs",
                        "args": {"skill_name": "cv-review"},
                        "id": "call-1",
                    }
                ],
            ),
            AIMessage(content="flag-0 claims a team size your profile never mentions."),
        ]
    )
    monkeypatch.setattr(review, "make_llm", lambda model: llm)

    brief = review.write_brief(_request(), JobRequirements(title="Backend Engineer"))

    assert brief == "flag-0 claims a team size your profile never mentions."
    assert [t.name for t in llm.bound_tools] == ["load_skill_from_fs"]
    # The skills catalog is offered up front; the body arrives as a tool result.
    assert "cv-review" in llm.conversations[0][0][1]
    assert "Ran a team of 40 engineers" in llm.conversations[0][1][1]
    tool_result = llm.conversations[1][-1]
    assert "Loaded skill: cv-review" in tool_result.content


def test_write_brief_returns_text_without_any_tool_call(monkeypatch):
    llm = ScriptedLLM([AIMessage(content="Nothing to look up.")])
    monkeypatch.setattr(review, "make_llm", lambda model: llm)
    assert review.write_brief(_request()) == "Nothing to look up."


def test_write_brief_handles_anthropic_content_blocks(monkeypatch):
    llm = ScriptedLLM(
        [AIMessage(content=[{"type": "text", "text": "Block-shaped answer."}])]
    )
    monkeypatch.setattr(review, "make_llm", lambda model: llm)
    assert review.write_brief(_request()) == "Block-shaped answer."


def test_a_failing_agent_degrades_to_no_brief(monkeypatch):
    def explode(model):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(review, "make_llm", explode)
    assert review.write_brief(_request()) == ""


def test_the_tool_budget_is_bounded(monkeypatch):
    # A model that only ever calls tools must not loop forever.
    call = AIMessage(
        content="",
        tool_calls=[
            {"name": "load_skill_from_fs", "args": {"skill_name": "cv-review"}, "id": "c"}
        ],
    )
    llm = ScriptedLLM([call] * 10)
    monkeypatch.setattr(review, "make_llm", lambda model: llm)
    monkeypatch.setattr(config, "REVIEW_MAX_TOOL_ITERATIONS", 3)
    assert review.write_brief(_request()) == ""
    assert len(llm.conversations) == 3


def test_disabling_the_agent_skips_the_call(monkeypatch):
    def explode(model):
        raise AssertionError("the LLM must not be called when disabled")

    monkeypatch.setattr(review, "make_llm", explode)
    monkeypatch.setattr(config, "REVIEW_AGENT_ENABLED", False)
    assert review.write_brief(_request()) == ""


def test_a_missing_skills_dir_degrades_gracefully(monkeypatch, tmp_path):
    # Same graceful degradation as the Phase 1.b nodes: no skills, no tools,
    # still a working brief.
    monkeypatch.setattr(config, "SKILLS_DIR", tmp_path / "absent")
    llm = ScriptedLLM([AIMessage(content="Brief without skills.")])
    monkeypatch.setattr(review, "make_llm", lambda model: llm)
    agent = review.ReviewAgent()
    assert agent.tools == []
    assert agent.write_brief(_request()) == "Brief without skills."
    assert llm.bound_tools is None

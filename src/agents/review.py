"""human_review node — the Phase 4 human-in-the-loop gate (design doc §8, §11).

Phases 1–3 reviewed validation flags *client-side*: `POST /tailor` returned
`validation.flags`, and a caller who wanted a document anyway re-ran the whole
graph with `approve_flagged=true`. That is a convention, not a guarantee — a
client that ignores the flags renders regardless, and re-running spends another
tailoring call and produces a *different* CV than the one that was reviewed.

Phase 4 moves the checkpoint into the graph. `human_review` calls LangGraph's
`interrupt()`, so the run stops between `validate_cv` and `render_document`
with its state checkpointed; the exact CV the person looked at is the one that
resumes. The reviewer answers per flagged item, and anything they do not
approve is **removed** from the CV before rendering — so the choice is never
"ship an unsupported claim or throw the run away".

This module is also where the FUND `AgentBase` machinery deferred in Phase 1.b
finally earns its place. The Phase 1 nodes are single-shot
`with_structured_output` calls whose skill is known in advance, so they resolve
it deterministically. Writing the reviewer's brief is the first genuinely
*agentic* step — the model decides which guidance it needs — so `ReviewAgent`
subclasses `AgentBase`, gets `skills/` loaded by `_load_skills`, and registers
FUND's runtime `load_skill_from_fs` tool so it can pull a full skill body (e.g.
`anti-fabrication`) mid-loop. `get_llm` is overridden onto this project's
`make_llm`, which keeps model tiering, provider switching and the single
test-mock point intact.
"""

import difflib
import json
import logging
import re

from langchain_core.messages import AIMessage, ToolMessage

from fund_models.agent_base import AgentBase, AgentConfig
from src import config
from src.agents.llm import make_llm
from src.chains.prompts import review_prompt
from src.models.schemas import (
    CareerProfile,
    JobRequirements,
    ReviewDecision,
    ReviewItem,
    ReviewRequest,
    TailoredCV,
    ValidationFlag,
    ValidationResult,
)

logger = logging.getLogger(__name__)


def _closest_profile_claim(profile: CareerProfile, claim: str) -> tuple[str | None, str | None]:
    """Nearest profile bullet to a flagged claim, with the source that backs it.

    The reviewer's real question is "is there something in my profile behind
    this?", so the brief shows the closest thing the gate could find rather
    than only saying it found nothing.
    """
    best_text: str | None = None
    best_ratio = 0.0
    best_source: str | None = None
    for exp in profile.experiences:
        for bullet in exp.bullets:
            ratio = difflib.SequenceMatcher(None, claim.lower(), bullet.lower()).ratio()
            if ratio > best_ratio:
                best_text, best_ratio, best_source = bullet, ratio, exp.source
    if best_text is None:
        return None, None
    return best_text, profile.raw_source_map.get(best_text, best_source)


def build_review_request(
    tailor_id: str,
    profile: CareerProfile,
    validation: ValidationResult,
    brief: str = "",
) -> ReviewRequest:
    """Turn the validation flags into the payload a human is asked to decide on.

    Args:
        tailor_id: The tailoring run being reviewed.
        profile: The canonical profile the claims were checked against.
        validation: The gate's result (its flags become the review items).
        brief: Optional reviewer-facing explanation from :class:`ReviewAgent`.

    Returns:
        A `ReviewRequest` whose items carry stable ids — the reviewer's
        `ReviewDecision.approvals` keys refer to them.
    """
    items = []
    for index, flag in enumerate(validation.flags):
        closest, source = _closest_profile_claim(profile, flag.item)
        items.append(
            ReviewItem(
                id=f"flag-{index}",
                item=flag.item,
                kind=flag.kind,
                reason=flag.reason,
                similarity=flag.similarity,
                closest_profile_text=closest,
                source=source,
            )
        )
    return ReviewRequest(tailor_id=tailor_id, items=items, brief=brief)


def _is_approved(item: ReviewItem, decision: ReviewDecision) -> bool:
    """Whether one flagged claim survives the reviewer's decision.

    Unlisted items default to **not** approved: silence is not consent for a
    claim the gate could not trace back to the profile.
    """
    if decision.approve_all:
        return True
    return bool(decision.approvals.get(item.id, False))


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _prose_terms(rejected: list[ReviewItem]) -> list[str]:
    """The words whose presence in prose would restate a rejected claim.

    A rejected *experience* is rendered by the gate as "<title> at <company>",
    which no summary would quote verbatim — the employer's name is what must
    not survive, so that is the term.
    """
    terms = []
    for item in rejected:
        if item.kind == "experience" and " at " in item.item:
            terms.append(item.item.split(" at ", 1)[1])
        else:
            terms.append(item.item)
    return [term for term in terms if term.strip()]


def _mentions(text: str, term: str) -> bool:
    """Whether `text` names `term` (case-insensitive, whole word/phrase)."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def _scrub_prose(text: str, terms: list[str]) -> str:
    """Drop whole sentences that name a rejected claim.

    The tailored `summary` and `headline` are free text, so removing a skill
    from `highlighted_skills` does not stop the same skill being asserted in
    the pitch above it. Dropping the sentence is deliberately blunt: a summary
    that reads a little thin is a fair price for one that says nothing the
    person rejected.

    Limitation: this catches *literal* mentions. A summary that paraphrases a
    rejected claim without naming it survives — the validation gate does not
    inspect prose at all, so nothing else catches it either.
    """
    if not text or not terms:
        return text
    kept = [
        sentence
        for sentence in _SENTENCE_BREAK.split(text)
        if not any(_mentions(sentence, term) for term in terms)
    ]
    return " ".join(kept).strip()


def _prune(
    cv: TailoredCV, rejected: list[ReviewItem], profile: CareerProfile | None = None
) -> TailoredCV:
    """Return a copy of the CV with every rejected claim removed."""
    if not rejected:
        return cv
    bullets = {i.item for i in rejected if i.kind == "bullet"}
    skills = {i.item.lower() for i in rejected if i.kind == "skill"}
    experiences = {i.item for i in rejected if i.kind == "experience"}
    projects = {i.item.lower() for i in rejected if i.kind == "project"}

    kept_experiences = []
    for exp in cv.selected_experiences:
        # ValidationFlag renders an experience as "<title> at <company>".
        if f"{exp.title} at {exp.company}" in experiences:
            continue
        kept_experiences.append(
            exp.model_copy(update={"bullets": [b for b in exp.bullets if b not in bullets]})
        )

    # The summary and headline are prose the gate never checked, so scrub them
    # too — otherwise a rejected skill simply moves from the skills line into
    # the pitch above it.
    terms = _prose_terms(rejected)
    headline = cv.headline
    if any(_mentions(headline, term) for term in terms):
        # A headline is one phrase; dropping it whole leaves nothing, so fall
        # back to the profile's own (sourced) headline.
        headline = (profile.headline or "") if profile else ""

    return cv.model_copy(
        update={
            "headline": headline,
            "summary": _scrub_prose(cv.summary, terms),
            "selected_experiences": kept_experiences,
            "selected_projects": [
                p for p in cv.selected_projects if p.name.lower() not in projects
            ],
            "highlighted_skills": [
                s for s in cv.highlighted_skills if s.lower() not in skills
            ],
        }
    )


def apply_decision(
    cv: TailoredCV,
    request: ReviewRequest,
    decision: ReviewDecision,
    profile: CareerProfile | None = None,
) -> tuple[TailoredCV, ValidationResult]:
    """Apply a human decision to the tailored CV and its validation result.

    Rejected claims are stripped from the CV — from the structured fields and
    from the summary/headline prose that may restate them. Approved ones stay
    and are kept in `flags` for provenance: a human accepted them, which is not
    the same as the gate having traced them.

    Args:
        cv: The tailored CV the reviewer saw.
        request: The review payload they answered.
        decision: Their per-item answer.
        profile: The canonical profile, used as the fallback headline when the
            tailored one names a rejected claim.

    Returns:
        The pruned CV and a post-review `ValidationResult` with
        `needs_review=False` (a person has now looked).
    """
    approved = [i for i in request.items if _is_approved(i, decision)]
    rejected = [i for i in request.items if not _is_approved(i, decision)]
    logger.info(
        "human_review: tailor %s — %d approved, %d removed",
        request.tailor_id,
        len(approved),
        len(rejected),
    )
    result = ValidationResult(
        passed=not approved,
        flags=[
            ValidationFlag(
                item=i.item, kind=i.kind, reason=i.reason, similarity=i.similarity
            )
            for i in approved
        ],
        needs_review=False,
    )
    return _prune(cv, rejected, profile), result


def _message_text(message) -> str:
    """Flatten a chat message's content (Anthropic returns content blocks)."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p).strip()


class ReviewAgent(AgentBase):
    """Tool-calling agent that writes the reviewer's brief.

    Unlike the Phase 1 nodes it is not handed a skill: it sees the skills
    catalog and pulls what it needs through FUND's `load_skill_from_fs` tool,
    which `AgentBase._load_skills` registers via :meth:`register_tool`.
    """

    def __init__(self) -> None:
        # _load_skills (called by AgentBase.__init__) registers the load_skill
        # tool through register_tool, so the registry must already exist.
        self.tools: list = []
        super().__init__(
            AgentConfig(
                agent_name="cv-review",
                llm_provider=config.LLM_PROVIDER,
                llm_model=config.REVIEW_MODEL,
                log_level=config.LOG_LEVEL,
            )
        )

    @property
    def skills_dir(self) -> str:
        """This project's skills live in `config.SKILLS_DIR`, not AgentConfig."""
        return str(config.SKILLS_DIR)

    def register_tool(self, tool) -> None:
        """Collect a runtime tool (FUND's DeepAgent hook, minimal here)."""
        self.tools.append(tool)

    def get_llm(self):
        """Use this project's factory: model tiering, provider switch, one mock point."""
        return make_llm(self.config.llm_model)

    def write_brief(
        self, request: ReviewRequest, job_requirements: JobRequirements | None = None
    ) -> str:
        """Explain the flagged items to the person deciding on them.

        Runs a bounded tool-calling loop so the model can load a skill body
        (typically `anti-fabrication`) before writing. Any failure degrades to
        an empty brief — the flags themselves are what gate rendering, and a
        missing explanation must never block the review.

        Args:
            request: The review payload (items only; `brief` is ignored).
            job_requirements: The posting, for context on why a claim was made.

        Returns:
            The brief, or `""` when the agent is disabled or the call failed.
        """
        if not config.REVIEW_AGENT_ENABLED:
            return ""
        try:
            return self._run_loop(request, job_requirements)
        except Exception as exc:  # noqa: BLE001 — never block the human gate
            logger.warning("review agent could not write a brief: %s", exc)
            return ""

    def _run_loop(
        self, request: ReviewRequest, job_requirements: JobRequirements | None
    ) -> str:
        llm = self.get_llm()
        if self.tools:
            llm = llm.bind_tools(self.tools)
        by_name = {t.name: t for t in self.tools}
        messages: list = [
            ("system", review_prompt.SYSTEM.format(skills_context=self.get_skills_context())),
            (
                "user",
                review_prompt.USER.format(
                    job_json=json.dumps(
                        job_requirements.model_dump() if job_requirements else {},
                        indent=2,
                        ensure_ascii=False,
                    ),
                    flags_json=json.dumps(
                        [i.model_dump() for i in request.items], indent=2, ensure_ascii=False
                    ),
                ),
            ),
        ]
        for _ in range(config.REVIEW_MAX_TOOL_ITERATIONS):
            response = llm.invoke(messages)
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                return _message_text(response)
            messages.append(
                response if isinstance(response, AIMessage) else AIMessage(content="")
            )
            for call in tool_calls:
                tool = by_name.get(call["name"])
                content = (
                    tool.invoke(call["args"])
                    if tool
                    else f"Unknown tool {call['name']!r}"
                )
                messages.append(ToolMessage(content=content, tool_call_id=call["id"]))
        logger.warning(
            "review agent hit the %d-iteration tool budget; writing no brief",
            config.REVIEW_MAX_TOOL_ITERATIONS,
        )
        return ""


def write_brief(
    request: ReviewRequest, job_requirements: JobRequirements | None = None
) -> str:
    """Module-level entry point used by the graph node (one agent per call)."""
    return ReviewAgent().write_brief(request, job_requirements)

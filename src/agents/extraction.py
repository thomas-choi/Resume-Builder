"""extract_source node — per-source structured extraction (Haiku)."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from src import config
from src.agents.llm import make_llm
from src.agents.skills import resolve_skill
from src.chains.prompts import extraction_prompt
from src.models.schemas import (
    Experience,
    Project,
    Skill,
    SourceDocument,
    SourceExtraction,
)
from src.tools.github_client import RepoChunk, render_repo_document, split_repo_sections

logger = logging.getLogger(__name__)

# Fields of SourceExtraction whose elements can be salvaged one at a time.
_ITEM_MODELS: dict[str, type[BaseModel]] = {
    "experiences": Experience,
    "projects": Project,
    "skills": Skill,
}


@dataclass
class ExtractionResult:
    """What one source yielded, plus what it cost to get there.

    Attributes:
        extraction: The merged structured extraction for the source.
        errors: One entry per item that could not be extracted, shaped for the
            API/UI: ``{"source", "repo", "reason"}``. ``repo`` is ``None`` when
            the failure was not attributable to a single repository.
        pruned_text: The source document rewritten without the failed repos, or
            ``None`` when nothing was dropped and the archive should stand as
            fetched.
    """

    extraction: SourceExtraction
    errors: list[dict] = field(default_factory=list)
    pruned_text: str | None = None


def extract_one(source: SourceDocument) -> ExtractionResult:
    """Run the extraction LLM on a single source document.

    The `source` field of every extracted experience/project is overwritten
    with the document id afterwards, so traceability never depends on the
    model following instructions.

    The strict `SourceExtraction` is still the tool schema handed to the model
    (it is what steers the output), but the response is requested with
    `include_raw=True` so a `ValidationError` is surfaced rather than raised.
    On that path the extraction is rebuilt item by item and only the offending
    entries are dropped — one malformed repo must not discard the other 29.

    A GitHub source is extracted a batch of repos at a time rather than in one
    call (see :func:`_extract_github`), because one document holding every repo
    asks for more structured output than the model reliably returns.

    Raises:
        ValidationError: if the response cannot be parsed and salvage recovers
            nothing usable. A silently empty profile is worse than an error.
    """
    logger.debug(
        "extract[%s]: type=%s, input %d chars, model=%s",
        source.id,
        source.source_type,
        len(source.raw_text),
        config.EXTRACTION_MODEL,
    )
    if source.source_type == "github":
        result = _extract_github(source)
    else:
        result = ExtractionResult(_extract_text(source, source.raw_text))

    extraction = result.extraction
    for exp in extraction.experiences:
        exp.source = source.id
    for proj in extraction.projects:
        proj.source = source.id
    logger.debug(
        "extract[%s]: %d experiences, %d projects, %d skills, %d education, "
        "%d certifications",
        source.id,
        len(extraction.experiences),
        len(extraction.projects),
        len(extraction.skills),
        len(extraction.education),
        len(extraction.certifications),
    )
    logger.debug(
        "extract[%s]: result:\n%s", source.id, extraction.model_dump_json(indent=2)
    )
    return result


def _extract_text(source: SourceDocument, raw_text: str) -> SourceExtraction:
    """One extraction call over `raw_text`, presented as `source`."""
    llm = make_llm(config.EXTRACTION_MODEL).with_structured_output(
        SourceExtraction, include_raw=True
    )
    response = llm.invoke(
        [
            (
                "system",
                extraction_prompt.SYSTEM.format(
                    skill=resolve_skill("source-extraction"),
                    source_id=source.id,
                ),
            ),
            (
                "user",
                extraction_prompt.USER.format(
                    source_type=source.source_type,
                    source_id=source.id,
                    structured=_structured_block(source),
                    raw_text=raw_text,
                ),
            ),
        ]
    )
    return _parse_response(response, source.id)


def _extract_github(source: SourceDocument) -> ExtractionResult:
    """Extract a GitHub source a batch of repos at a time, isolating failures.

    One `github_username` produces exactly one document holding every repo, so
    a failure anywhere in it previously cost the whole source — ~50 repos lost
    because the model returned a message with no tool call. Repos are therefore
    extracted `GITHUB_REPOS_PER_EXTRACTION` at a time, and a failed batch is
    retried **one repo at a time** so the blame lands on a specific repository
    instead of on the other forty-nine.

    Every batch document carries the header and the tier headings of the repos
    in it, because those headings are what tell the extractor whether a repo is
    the user's own work or someone else's project they contributed to.
    """
    header, chunks = split_repo_sections(source.raw_text)
    if not chunks:
        # No repo structure to split on (e.g. an account with no repos) — this
        # is an ordinary single-call document.
        return ExtractionResult(_extract_text(source, source.raw_text))

    size = max(1, config.GITHUB_REPOS_PER_EXTRACTION)
    batches = [chunks[i : i + size] for i in range(0, len(chunks), size)]
    logger.debug(
        "extract[%s]: %d repos in %d batches of up to %d",
        source.id,
        len(chunks),
        len(batches),
        size,
    )

    parts: list[SourceExtraction] = []
    kept: list[RepoChunk] = []
    errors: list[dict] = []
    for index, batch in enumerate(batches, start=1):
        try:
            parts.append(_extract_text(source, render_repo_document(header, batch)))
            kept.extend(batch)
            continue
        except Exception as exc:  # noqa: BLE001 — isolate, then attribute
            logger.warning(
                "extract[%s]: batch %d/%d (%s) failed, retrying its %d repos "
                "one at a time: %s",
                source.id,
                index,
                len(batches),
                ", ".join(chunk.repo for chunk in batch),
                len(batch),
                exc,
            )
        for chunk in batch:
            try:
                parts.append(
                    _extract_text(source, render_repo_document(header, [chunk]))
                )
                kept.append(chunk)
            except Exception as exc:  # noqa: BLE001 — one repo must not be fatal
                logger.error(
                    "extract[%s]: repo %s could not be extracted, dropping it: %s",
                    source.id,
                    chunk.repo,
                    exc,
                )
                errors.append(
                    {
                        "source": source.id,
                        "repo": chunk.repo,
                        "reason": short_reason(exc),
                    }
                )

    if not parts:
        raise ValueError(
            f"extraction[{source.id}] failed for all {len(chunks)} repositories"
        )
    if errors:
        logger.warning(
            "extract[%s]: %d of %d repos extracted, %d dropped: %s",
            source.id,
            len(kept),
            len(chunks),
            len(errors),
            ", ".join(err["repo"] for err in errors),
        )
    return ExtractionResult(
        extraction=_merge_extractions(parts),
        errors=errors,
        # Only rewrite the archive when something was actually dropped; an
        # untouched document re-renders byte-identically, so this is about
        # intent, not bytes.
        pruned_text=render_repo_document(header, kept) if errors else None,
    )


def _merge_extractions(parts: list[SourceExtraction]) -> SourceExtraction:
    """Combine per-batch extractions of one source into a single extraction.

    List fields concatenate; the scalar identity fields take the first non-empty
    value seen. Duplicates across batches (the same skill named in several
    repos) are left for synthesis, which already dedupes across sources.
    """
    if len(parts) == 1:
        return parts[0]
    merged = SourceExtraction()
    for part in parts:
        merged.name = merged.name or part.name
        merged.headline = merged.headline or part.headline
        merged.contact = merged.contact or part.contact
        merged.experiences.extend(part.experiences)
        merged.projects.extend(part.projects)
        merged.education.extend(part.education)
        merged.skills.extend(part.skills)
        merged.certifications.extend(part.certifications)
    return merged


def short_reason(exc: Exception) -> str:
    """One-line failure reason, short enough for an API response and a UI list."""
    reason = " ".join(str(exc).split())
    return reason[:200] if reason else exc.__class__.__name__


def _structured_block(source: SourceDocument) -> str:
    """Render the authoritative-records block for a source that carries one.

    Sources parsed from an official data export (Phase 2: LinkedIn) hand the
    model exported records rather than prose; those beat the flattened text
    rendering. Prose sources (CVs, GitHub, free text) get an empty block, so
    their prompt is byte-for-byte what it was before.
    """
    if not source.structured_fields:
        return ""
    return extraction_prompt.STRUCTURED.format(
        source_type=source.source_type,
        structured_fields=json.dumps(
            source.structured_fields, indent=2, ensure_ascii=False
        ),
    )


def _parse_response(response: Any, source_id: str) -> SourceExtraction:
    """Unwrap an `include_raw=True` response, salvaging items if it failed."""
    if isinstance(response, SourceExtraction):  # provider ignored include_raw
        return response
    error = response.get("parsing_error")
    parsed = response.get("parsed")
    if error is None and parsed is not None:
        return parsed
    raw = response.get("raw")
    logger.warning(
        "extract[%s]: structured output failed to validate, attempting "
        "item-level salvage: error=%s; %s",
        source_id,
        error,
        _diagnostics(raw),
    )
    return _salvage(raw, source_id, error)


def _diagnostics(raw: Any) -> str:
    """Why a response was unusable, in terms a log reader can act on.

    Both `parsed` and `parsing_error` come back `None` when the model returns a
    message with no tool call at all — the failure that lost a 50-repo GitHub
    source and logged only `: None`. The provider metadata is where the actual
    cause lives (a `max_tokens` finish reason, a refusal in the content), so it
    is reported rather than the empty error.
    """
    if raw is None:
        return "no raw response"
    metadata = getattr(raw, "response_metadata", None) or {}
    content = getattr(raw, "content", "") or ""
    if not isinstance(content, str):
        content = str(content)
    return (
        f"tool_calls={len(getattr(raw, 'tool_calls', None) or [])}, "
        f"finish_reason={metadata.get('finish_reason') or metadata.get('stop_reason')!r}, "
        f"usage={getattr(raw, 'usage_metadata', None)}, "
        f"response_metadata={metadata}, "
        f"content[:300]={content[:300]!r}"
    )


def _salvage(raw: Any, source_id: str, error: Exception | None) -> SourceExtraction:
    """Rebuild a `SourceExtraction` field by field from raw tool-call args.

    List fields of extraction items are validated one element at a time so a
    single bad entry costs only itself. Anything that cannot be salvaged at
    all re-raises `error`.
    """
    args = _tool_call_args(raw)
    if args is None:
        logger.error(
            "extract[%s]: no parseable tool-call arguments to salvage from; %s",
            source_id,
            _diagnostics(raw),
        )
        raise error or ValueError(f"extraction[{source_id}] returned no usable output")

    fields: dict[str, Any] = {}
    attempted = recovered = 0
    for field, value in args.items():
        if field not in SourceExtraction.model_fields:
            continue
        item_model = _ITEM_MODELS.get(field)
        if item_model is not None and isinstance(value, list):
            attempted += len(value)
            items = _salvage_items(value, item_model, field, source_id)
            recovered += len(items)
            fields[field] = items
        elif _field_is_valid(field, value):
            fields[field] = value
        else:
            logger.warning(
                "extract[%s]: dropped unparseable field %r", source_id, field
            )

    if attempted and not recovered:
        logger.error(
            "extract[%s]: salvage recovered none of %d items", source_id, attempted
        )
        raise error or ValueError(f"extraction[{source_id}] returned no usable items")
    try:
        extraction = SourceExtraction.model_validate(fields)
    except ValidationError:
        logger.error("extract[%s]: salvaged fields still do not validate", source_id)
        raise error or ValueError(f"extraction[{source_id}] returned no usable output")
    logger.warning(
        "extract[%s]: salvaged %d of %d items", source_id, recovered, attempted
    )
    return extraction


def _tool_call_args(raw: Any) -> dict | None:
    """Pull the first tool call's arguments off a raw LLM message."""
    tool_calls = getattr(raw, "tool_calls", None)
    if not tool_calls:
        return None
    args = tool_calls[0].get("args") if isinstance(tool_calls[0], dict) else None
    return args if isinstance(args, dict) else None


def _salvage_items(
    values: list, item_model: type[BaseModel], field: str, source_id: str
) -> list:
    """Validate list elements individually, logging and dropping the failures."""
    items = []
    for index, value in enumerate(values):
        try:
            items.append(item_model.model_validate(value))
        except ValidationError as exc:
            name = value.get("name") if isinstance(value, dict) else None
            logger.warning(
                "extract[%s]: dropped %s[%d] (name=%r): %s",
                source_id,
                field,
                index,
                name,
                exc,
            )
    return items


def _field_is_valid(field: str, value: Any) -> bool:
    """Whether one top-level field survives validation on its own."""
    try:
        SourceExtraction.model_validate({field: value})
    except ValidationError:
        return False
    return True

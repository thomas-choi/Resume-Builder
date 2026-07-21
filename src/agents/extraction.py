"""extract_source node — per-source structured extraction (Haiku)."""

import logging
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

logger = logging.getLogger(__name__)

# Fields of SourceExtraction whose elements can be salvaged one at a time.
_ITEM_MODELS: dict[str, type[BaseModel]] = {
    "experiences": Experience,
    "projects": Project,
    "skills": Skill,
}


def extract_one(source: SourceDocument) -> SourceExtraction:
    """Run the extraction LLM on a single source document.

    The `source` field of every extracted experience/project is overwritten
    with the document id afterwards, so traceability never depends on the
    model following instructions.

    The strict `SourceExtraction` is still the tool schema handed to the model
    (it is what steers the output), but the response is requested with
    `include_raw=True` so a `ValidationError` is surfaced rather than raised.
    On that path the extraction is rebuilt item by item and only the offending
    entries are dropped — one malformed repo must not discard the other 29.

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
                    raw_text=source.raw_text,
                ),
            ),
        ]
    )
    extraction = _parse_response(response, source.id)
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
    return extraction


def _parse_response(response: Any, source_id: str) -> SourceExtraction:
    """Unwrap an `include_raw=True` response, salvaging items if it failed."""
    if isinstance(response, SourceExtraction):  # provider ignored include_raw
        return response
    error = response.get("parsing_error")
    parsed = response.get("parsed")
    if error is None and parsed is not None:
        return parsed
    logger.warning(
        "extract[%s]: structured output failed to validate, attempting "
        "item-level salvage: %s",
        source_id,
        error,
    )
    return _salvage(response.get("raw"), source_id, error)


def _salvage(raw: Any, source_id: str, error: Exception | None) -> SourceExtraction:
    """Rebuild a `SourceExtraction` field by field from raw tool-call args.

    List fields of extraction items are validated one element at a time so a
    single bad entry costs only itself. Anything that cannot be salvaged at
    all re-raises `error`.
    """
    args = _tool_call_args(raw)
    if args is None:
        logger.error(
            "extract[%s]: no parseable tool-call arguments to salvage from", source_id
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

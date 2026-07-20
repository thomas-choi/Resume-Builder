"""extract_source node — per-source structured extraction (Haiku)."""

import logging

from src import config
from src.agents.llm import make_llm
from src.chains.prompts import extraction_prompt
from src.models.schemas import SourceDocument, SourceExtraction

logger = logging.getLogger(__name__)


def extract_one(source: SourceDocument) -> SourceExtraction:
    """Run the extraction LLM on a single source document.

    The `source` field of every extracted experience/project is overwritten
    with the document id afterwards, so traceability never depends on the
    model following instructions.
    """
    logger.debug(
        "extract[%s]: type=%s, input %d chars, model=%s",
        source.id,
        source.source_type,
        len(source.raw_text),
        config.EXTRACTION_MODEL,
    )
    llm = make_llm(config.EXTRACTION_MODEL).with_structured_output(SourceExtraction)
    extraction: SourceExtraction = llm.invoke(
        [
            (
                "system",
                extraction_prompt.SYSTEM.format(source_id=source.id),
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

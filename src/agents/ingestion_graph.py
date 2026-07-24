"""Ingestion graph: ingest_sources -> extract_source -> synthesize_profile -> store_profile."""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import extraction, synthesis
from src.models.schemas import CareerProfile, SourceDocument, SourceExtraction
from src.utils import profile_store, run_store

import logging

logger = logging.getLogger(__name__)

class IngestionState(TypedDict, total=False):
    # The owner's email (the user-id, §14.8). Threaded from the route so every
    # store write lands under this account's root; never read from user input.
    email: str
    run_id: str
    sources: list[SourceDocument]
    extractions: list[SourceExtraction]
    # Items that could not be extracted: {"source", "repo", "reason"}. Surfaced
    # to the caller and the UI — a run that quietly lost a whole source is what
    # made this class of bug expensive to find.
    source_errors: list[dict]
    # source id -> the document rewritten without its failed repos, applied to
    # the run archive by store_profile.
    pruned_sources: dict[str, str]
    profile: CareerProfile
    profile_id: str
    version: int


def ingest_sources(state: IngestionState) -> IngestionState:
    """Validate that at least one non-empty source was provided."""
    sources = [s for s in state.get("sources", []) if s.raw_text.strip()]
    logger.debug(f"** Ingesting {len(sources)} non-empty sources: {[s.id for s in sources]}")
    if not sources:
        raise ValueError("no non-empty sources provided")
    return {"sources": sources}


def extract_source(state: IngestionState) -> IngestionState:
    """Run per-source LLM extraction (one call per source document).

    A hard failure on one source (provider error, no parseable response at
    all) is logged and skipped so the surviving sources still produce a
    profile; if *every* source fails there is nothing to synthesize and the
    error is raised. Item-level resilience lives in ``extract_one`` — losing a
    source here still means losing that whole document.
    """
    sources = state["sources"]
    logger.debug(f"** Extracting from {len(sources)} sources")
    extractions = []
    errors: list[dict] = []
    pruned: dict[str, str] = {}
    last_error: Exception | None = None
    for source in sources:
        try:
            result = extraction.extract_one(source)
        except Exception as exc:  # noqa: BLE001 — one dead source must not be fatal
            last_error = exc
            logger.error("extraction failed for source %s: %s", source.id, exc)
            errors.append(
                {
                    "source": source.id,
                    "repo": None,
                    "reason": extraction.short_reason(exc),
                }
            )
            continue
        extractions.append(result.extraction)
        errors.extend(result.errors)
        if result.pruned_text is not None:
            pruned[source.id] = result.pruned_text
    if not extractions:
        raise last_error or ValueError("no sources could be extracted")
    return {"extractions": extractions, "source_errors": errors, "pruned_sources": pruned}


def synthesize_profile(state: IngestionState) -> IngestionState:
    """Merge extractions into one canonical CareerProfile."""
    logger.debug(f"** Synthesizing profile from {len(state['extractions'])} extractions")
    return {"profile": synthesis.synthesize(state["extractions"])}


def _prune_archived_sources(email: str, run_id: str, state: IngestionState) -> None:
    """Rewrite archived sources whose failed items were dropped mid-extraction.

    File writes for a run live in one node on purpose, so `extract_source` can
    stay a pure LLM step. A failure here is logged and swallowed: the profile is
    already built, and losing the bookkeeping must not lose the run.
    """
    pruned = state.get("pruned_sources") or {}
    if not pruned:
        return
    for source in state.get("sources") or []:
        text = pruned.get(source.id)
        if text is None or not source.stored_path:
            continue
        path = Path(source.stored_path)
        try:
            raw_path = run_store.prune_source_document(path, text)
            run_store.add_source_entry(
                email,
                run_id,
                run_store.source_entry(
                    source.source_type,
                    raw_path,
                    raw_path.read_bytes(),
                    source_id=f"{source.id}#as-fetched",
                ),
            )
        # ValueError covers a corrupt archive (JSONDecodeError); the profile is
        # already stored, so bookkeeping must not be able to fail the run.
        except (OSError, ValueError) as exc:
            logger.warning(
                "could not prune archived source %s (%s): %s", source.id, path, exc
            )


def store_profile(state: IngestionState) -> IngestionState:
    """Persist the profile as a new version, and archive an output copy per run.

    The canonical store is the versioned profile store; when a ``run_id`` is
    present we additionally save ``data/output/{run_id}/output.json`` and link
    the run's manifest to the produced ``profile_id`` / ``version`` for
    end-to-end provenance.
    """
    logger.debug(f"** Storing profile")
    email = state["email"]
    profile_id, version = profile_store.save_profile(
        email, state["profile"], state.get("profile_id")
    )
    logger.debug(f"** Storing profile {profile_id} version {version}")
    run_id = state.get("run_id")
    if run_id:
        _prune_archived_sources(email, run_id, state)
        run_store.save_output(
            email,
            run_id,
            state["profile"],
            {"profile_id": profile_id, "version": version},
        )
        logger.debug(f"** Saved output for run {run_id} -> profile {profile_id} version {version}")
    return {"profile_id": profile_id, "version": version}


def build_ingestion_graph():
    """Compile the ingestion StateGraph."""
    graph = StateGraph(IngestionState)
    graph.add_node("ingest_sources", ingest_sources)
    graph.add_node("extract_source", extract_source)
    graph.add_node("synthesize_profile", synthesize_profile)
    graph.add_node("store_profile", store_profile)
    graph.add_edge(START, "ingest_sources")
    graph.add_edge("ingest_sources", "extract_source")
    graph.add_edge("extract_source", "synthesize_profile")
    graph.add_edge("synthesize_profile", "store_profile")
    graph.add_edge("store_profile", END)
    return graph.compile()

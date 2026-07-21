"""Ingestion graph: ingest_sources -> extract_source -> synthesize_profile -> store_profile."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import extraction, synthesis
from src.models.schemas import CareerProfile, SourceDocument, SourceExtraction
from src.utils import profile_store, run_store

import logging

logger = logging.getLogger(__name__)

class IngestionState(TypedDict, total=False):
    run_id: str
    sources: list[SourceDocument]
    extractions: list[SourceExtraction]
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
    last_error: Exception | None = None
    for source in sources:
        try:
            extractions.append(extraction.extract_one(source))
        except Exception as exc:  # noqa: BLE001 — one dead source must not be fatal
            last_error = exc
            logger.error("extraction failed for source %s: %s", source.id, exc)
    if not extractions:
        raise last_error or ValueError("no sources could be extracted")
    return {"extractions": extractions}


def synthesize_profile(state: IngestionState) -> IngestionState:
    """Merge extractions into one canonical CareerProfile."""
    logger.debug(f"** Synthesizing profile from {len(state['extractions'])} extractions")
    return {"profile": synthesis.synthesize(state["extractions"])}


def store_profile(state: IngestionState) -> IngestionState:
    """Persist the profile as a new version, and archive an output copy per run.

    The canonical store is the versioned profile store; when a ``run_id`` is
    present we additionally save ``data/output/{run_id}/output.json`` and link
    the run's manifest to the produced ``profile_id`` / ``version`` for
    end-to-end provenance.
    """
    logger.debug(f"** Storing profile")
    profile_id, version = profile_store.save_profile(
        state["profile"], state.get("profile_id")
    )
    logger.debug(f"** Storing profile {profile_id} version {version}")
    run_id = state.get("run_id")
    if run_id:
        run_store.save_output(
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

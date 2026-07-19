"""Ingestion graph: ingest_sources -> extract_source -> synthesize_profile -> store_profile."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from src.agents import extraction, synthesis
from src.models.schemas import CareerProfile, SourceDocument, SourceExtraction
from src.utils import profile_store


class IngestionState(TypedDict, total=False):
    sources: list[SourceDocument]
    extractions: list[SourceExtraction]
    profile: CareerProfile
    profile_id: str
    version: int


def ingest_sources(state: IngestionState) -> IngestionState:
    """Validate that at least one non-empty source was provided."""
    sources = [s for s in state.get("sources", []) if s.raw_text.strip()]
    if not sources:
        raise ValueError("no non-empty sources provided")
    return {"sources": sources}


def extract_source(state: IngestionState) -> IngestionState:
    """Run per-source LLM extraction (one call per source document)."""
    return {"extractions": [extraction.extract_one(s) for s in state["sources"]]}


def synthesize_profile(state: IngestionState) -> IngestionState:
    """Merge extractions into one canonical CareerProfile."""
    return {"profile": synthesis.synthesize(state["extractions"])}


def store_profile(state: IngestionState) -> IngestionState:
    """Persist the profile as v1 in the versioned JSON store."""
    profile_id, version = profile_store.save_profile(
        state["profile"], state.get("profile_id")
    )
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

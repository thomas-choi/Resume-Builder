"""REST + SSE routes for the resume builder API."""

import asyncio
import json
import tempfile
import uuid
from pathlib import Path

import anyio
from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agents.ingestion_graph import build_ingestion_graph
from src.agents.tailoring_graph import build_tailoring_graph
from src.models.schemas import CareerProfile, SourceDocument
from src.tools.docx_reader import read_docx
from src.tools.github_client import fetch_github_profile, free_text_source
from src.tools.pdf_reader import read_pdf
from src.utils import profile_store

router = APIRouter()

_DONE = {"event": "done"}


class JobRegistry:
    """In-process registry of per-job asyncio queues for SSE progress."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def create(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[job_id] = queue
        return queue

    def get(self, job_id: str) -> asyncio.Queue | None:
        return self._queues.get(job_id)

    def discard(self, job_id: str) -> None:
        self._queues.pop(job_id, None)


jobs = JobRegistry()


def _load_upload(upload: UploadFile) -> SourceDocument:
    """Persist an uploaded CV to a temp file and parse it by extension."""
    suffix = Path(upload.filename or "cv").suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise HTTPException(400, f"unsupported CV file type: {suffix or '(none)'}")
    with tempfile.NamedTemporaryFile(
        suffix=suffix, prefix=Path(upload.filename or "cv").stem + "-", delete=False
    ) as tmp:
        tmp.write(upload.file.read())
        tmp_path = Path(tmp.name)
    try:
        doc = read_docx(tmp_path) if suffix == ".docx" else read_pdf(tmp_path)
        # Keep the original filename in the source id, not the temp name.
        doc.id = f"{doc.source_type}:{upload.filename}"
        return doc
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.post("/ingest")
async def ingest(
    cv: list[UploadFile] | None = None,
    github_username: str | None = Form(default=None),
    free_text: str | None = Form(default=None),
    job_id: str | None = Form(default=None),
) -> dict:
    """Run the ingestion graph over the provided sources.

    Pass a client-generated `job_id` and subscribe to
    `GET /ingest/{job_id}/events` before/while POSTing to watch per-node
    progress; otherwise a server-generated job_id is returned.
    """
    sources: list[SourceDocument] = []
    for upload in cv or []:
        sources.append(_load_upload(upload))
    if github_username:
        sources.append(
            await anyio.to_thread.run_sync(fetch_github_profile, github_username)
        )
    if free_text and free_text.strip():
        sources.append(free_text_source(free_text))
    if not sources:
        raise HTTPException(400, "provide at least one source (cv, github_username, free_text)")

    job_id = job_id or uuid.uuid4().hex[:12]
    queue = jobs.create(job_id)
    loop = asyncio.get_running_loop()

    def publish(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_graph() -> dict:
        graph = build_ingestion_graph()
        state: dict = {}
        for update in graph.stream({"sources": sources}, stream_mode="updates"):
            for node, node_state in update.items():
                publish({"event": "node", "data": node})
                state.update(node_state or {})
        return state

    try:
        state = await anyio.to_thread.run_sync(run_graph)
    except Exception as exc:
        publish({"event": "error", "data": str(exc)})
        publish(_DONE)
        raise HTTPException(500, f"ingestion failed: {exc}") from exc
    publish(_DONE)

    profile: CareerProfile = state["profile"]
    return {
        "job_id": job_id,
        "profile_id": state["profile_id"],
        "version": state["version"],
        "profile": profile.model_dump(),
    }


@router.get("/ingest/{job_id}/events")
async def ingest_events(job_id: str) -> EventSourceResponse:
    """SSE stream of per-node progress for an ingestion job."""
    queue = jobs.get(job_id) or jobs.create(job_id)

    async def event_stream():
        try:
            while True:
                event = await queue.get()
                if event is _DONE or event.get("event") == "done":
                    yield {"event": "done", "data": ""}
                    break
                yield {"event": event["event"], "data": event.get("data", "")}
        finally:
            jobs.discard(job_id)

    return EventSourceResponse(event_stream())


@router.get("/profile/{profile_id}")
def get_profile(profile_id: str, version: int | None = None) -> dict:
    """Fetch a stored profile (latest version by default)."""
    try:
        profile = profile_store.load_profile(profile_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "profile_id": profile_id,
        "version": version or profile_store.latest_version(profile_id),
        "versions": profile_store.list_versions(profile_id),
        "profile": profile.model_dump(),
    }


@router.put("/profile/{profile_id}")
def put_profile(profile_id: str, profile: CareerProfile) -> dict:
    """Save a user-edited profile as a new version (v1 conflict resolution)."""
    if profile_store.latest_version(profile_id) == 0:
        raise HTTPException(404, f"profile {profile_id} not found")
    _, version = profile_store.save_profile(profile, profile_id)
    return {"profile_id": profile_id, "version": version}


class TailorRequest(BaseModel):
    profile_id: str
    job_post: str
    version: int | None = None


@router.post("/tailor")
async def tailor(request: TailorRequest) -> dict:
    """Run the tailoring graph: job analysis -> tailoring -> validation."""
    try:
        profile = profile_store.load_profile(request.profile_id, request.version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not request.job_post.strip():
        raise HTTPException(400, "job_post must not be empty")

    def run_graph() -> dict:
        graph = build_tailoring_graph()
        return graph.invoke({"profile": profile, "job_post": request.job_post})

    state = await anyio.to_thread.run_sync(run_graph)
    return {
        "profile_id": request.profile_id,
        "job_requirements": state["job_requirements"].model_dump(),
        "tailored_cv": state["tailored_cv"].model_dump(),
        "validation": state["validation"].model_dump(),
    }

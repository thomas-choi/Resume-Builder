"""REST + SSE routes for the resume builder API."""

import asyncio
import re
import uuid
from pathlib import Path

import anyio
from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agents.ingestion_graph import build_ingestion_graph
from src.agents.tailoring_graph import build_tailoring_graph
from src.models.schemas import CareerProfile, SourceDocument
from src.tools.docx_reader import read_docx
from src.tools.github_client import fetch_github_profile, free_text_source
from src.tools.linkedin_export import read_linkedin_export
from src.tools.pdf_reader import read_pdf
from src.utils import document_store, profile_store, run_store
from src.utils.logging_setup import set_run_id

router = APIRouter()

_DONE = {"event": "done"}

_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


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

# A profile_id becomes a directory name under data/profiles/, so restrict it to
# safe filename characters (no path separators / traversal).
_PROFILE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _validate_profile_id(profile_id: str) -> str:
    """Sanitize a caller-supplied profile_id or raise HTTP 400."""
    profile_id = profile_id.strip()
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        raise HTTPException(
            400,
            "profile_id must be 1-64 characters of letters, digits, '-' or '_'",
        )
    return profile_id


def _load_upload(run_id: str, upload: UploadFile) -> tuple[SourceDocument, dict]:
    """Archive an uploaded CV under the run's sources dir, then parse it.

    The raw bytes are persisted to ``data/sources/{run_id}/cv/<original-name>``
    *before* parsing, so an upload survives even if the graph later fails and
    the run can still be reconstructed. Returns the parsed source document and
    its manifest entry.
    """
    filename = upload.filename or "cv"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise HTTPException(400, f"unsupported CV file type: {suffix or '(none)'}")
    data = upload.file.read()
    stored = run_store.save_source_file(run_id, "cv", filename, data)
    doc = read_docx(stored) if suffix == ".docx" else read_pdf(stored)
    # Keep the original filename in the source id, not the stored path.
    doc.id = f"{doc.source_type}:{filename}"
    doc.stored_path = str(stored)
    return doc, run_store.source_entry("cv", stored, data, source_id=doc.id)


def _load_linkedin_export(run_id: str, upload: UploadFile) -> tuple[SourceDocument, dict]:
    """Archive an uploaded LinkedIn data export, then parse it.

    Same order as :func:`_load_upload`: the raw archive is persisted under
    ``data/sources/{run_id}/linkedin/<original-name>`` *before* parsing, so a
    rejected export is still on disk to inspect. Parsing is deterministic (no
    LLM) and never scrapes LinkedIn — the person's own export is the only
    supported input.
    """
    filename = upload.filename or "linkedin-export.zip"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".zip", ".csv"):
        raise HTTPException(
            400, f"unsupported LinkedIn export file type: {suffix or '(none)'}"
        )
    data = upload.file.read()
    stored = run_store.save_source_file(run_id, "linkedin", filename, data)
    try:
        doc = read_linkedin_export(stored)
    except ValueError as exc:
        raise HTTPException(400, f"unreadable LinkedIn export: {exc}") from exc
    # Keep the original filename in the source id, not the stored path.
    doc.id = f"linkedin:{filename}"
    doc.stored_path = str(stored)
    return doc, run_store.source_entry("linkedin", stored, data, source_id=doc.id)


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.post("/ingest")
async def ingest(
    cv: list[UploadFile] | None = None,
    linkedin_export: list[UploadFile] | None = None,
    github_username: str | None = Form(default=None),
    free_text: str | None = Form(default=None),
    job_id: str | None = Form(default=None),
    profile_id: str | None = Form(default=None),
) -> dict:
    """Run the ingestion graph over the provided sources.

    Sources are CV file(s), LinkedIn data export(s) (the official ZIP the
    person downloads, or single CSVs from it), a GitHub username, and pasted
    free text — any combination, at least one.

    Pass a client-generated `job_id` and subscribe to
    `GET /ingest/{job_id}/events` before/while POSTing to watch per-node
    progress; otherwise a server-generated job_id is returned. The same id is
    used as the `run_id`: raw inputs are archived under
    `data/sources/{run_id}/` and the output copy under `data/output/{run_id}/`.

    Pass `profile_id` to direct the result into a specific profile: an existing
    one gets a new version appended, a new id is created at v1. Omit it and the
    server mints a fresh profile_id (the default).
    """
    # Allocate the run/correlation id up front so raw inputs can be archived
    # before parsing (and before the graph runs). job_id doubles as run_id.
    run_id = job_id or uuid.uuid4().hex[:12]
    set_run_id(run_id)
    if profile_id is not None:
        profile_id = _validate_profile_id(profile_id)

    sources: list[SourceDocument] = []
    manifest_entries: list[dict] = []
    for upload in cv or []:
        doc, entry = _load_upload(run_id, upload)
        sources.append(doc)
        manifest_entries.append(entry)
    for upload in linkedin_export or []:
        doc, entry = _load_linkedin_export(run_id, upload)
        sources.append(doc)
        manifest_entries.append(entry)
    if github_username:
        gh_doc = await anyio.to_thread.run_sync(fetch_github_profile, github_username)
        gh_bytes = gh_doc.model_dump_json(indent=2).encode("utf-8")
        gh_path = run_store.save_source_file(run_id, "github", "github.json", gh_bytes)
        gh_doc.stored_path = str(gh_path)
        sources.append(gh_doc)
        manifest_entries.append(
            run_store.source_entry("github", gh_path, gh_bytes, source_id=gh_doc.id)
        )
    if free_text and free_text.strip():
        ft_doc = free_text_source(free_text)
        # free_text is also the LinkedIn-summary path (PLAN.md Phase 2 maps
        # LinkedIn through here); archive it as linkedin-summary.txt.
        ft_bytes = ft_doc.raw_text.encode("utf-8")
        ft_path = run_store.save_source_file(
            run_id, "linkedin", "linkedin-summary.txt", ft_bytes
        )
        ft_doc.stored_path = str(ft_path)
        sources.append(ft_doc)
        manifest_entries.append(
            run_store.source_entry("linkedin", ft_path, ft_bytes, source_id=ft_doc.id)
        )
    if not sources:
        raise HTTPException(
            400,
            "provide at least one source (cv, linkedin_export, github_username, free_text)",
        )

    run_store.write_manifest(run_id, manifest_entries)

    queue = jobs.create(run_id)
    loop = asyncio.get_running_loop()

    def publish(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_graph() -> dict:
        set_run_id(run_id)
        graph = build_ingestion_graph()
        state: dict = {}
        stream_input: dict = {"run_id": run_id, "sources": sources}
        if profile_id:
            # store_profile threads this to save_profile: existing id → new
            # version, unknown id → created at v1.
            stream_input["profile_id"] = profile_id
        for update in graph.stream(stream_input, stream_mode="updates"):
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
        "job_id": run_id,
        "run_id": run_id,
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
    render: bool = False
    cover_letter: bool = False
    approve_flagged: bool = False


@router.post("/tailor")
async def tailor(request: TailorRequest) -> dict:
    """Run the tailoring graph: job analysis -> tailoring -> validation -> render.

    Set `render` to also produce document files (`.docx`, plus `.pdf` when
    LibreOffice is available), downloadable from
    `GET /document/{tailor_id}`. Set `cover_letter` to generate one alongside
    the CV; it is returned in the response either way, and rendered too when
    `render` is set.

    Rendering is gated by the validation result: when `validation.needs_review`
    is true nothing is written, and `render_skipped` explains why. Review the
    flags, then re-run with `approve_flagged` to render regardless.
    """
    try:
        profile = profile_store.load_profile(request.profile_id, request.version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not request.job_post.strip():
        raise HTTPException(400, "job_post must not be empty")

    tailor_id = uuid.uuid4().hex[:12]

    def run_graph() -> dict:
        graph = build_tailoring_graph()
        return graph.invoke(
            {
                "profile": profile,
                "job_post": request.job_post,
                "tailor_id": tailor_id,
                "render": request.render,
                "want_cover_letter": request.cover_letter,
                "approved": request.approve_flagged,
            }
        )

    state = await anyio.to_thread.run_sync(run_graph)
    cover_letter = state.get("cover_letter")
    documents = state.get("documents") or []
    response = {
        "profile_id": request.profile_id,
        "tailor_id": tailor_id,
        "job_requirements": state["job_requirements"].model_dump(),
        "tailored_cv": state["tailored_cv"].model_dump(),
        "validation": state["validation"].model_dump(),
        "cover_letter": cover_letter.model_dump() if cover_letter else None,
        "documents": [
            {**doc, "url": f"/document/{tailor_id}?kind={doc['kind']}&format={doc['format']}"}
            for doc in documents
        ],
        "render_skipped": state.get("render_skipped"),
    }
    # Persist the result next to the documents so a download can be traced back
    # to the claims the validation gate checked (and Phase 4 can resume from it).
    document_store.save_result(
        tailor_id,
        {k: v for k, v in response.items() if k != "documents"},
    )
    return response


@router.get("/document/{tailor_id}")
def get_document(tailor_id: str, kind: str = "cv", format: str = "docx") -> FileResponse:
    """Download a document rendered by `POST /tailor`.

    Args:
        tailor_id: The `tailor_id` returned by `POST /tailor`.
        kind: `cv` (default) or `cover_letter`.
        format: `docx` (default) or `pdf`.

    Returns:
        The file. 404 when that document was not rendered — because `render`
        was not set, the validation gate skipped it, or (for `pdf`) LibreOffice
        was unavailable.
    """
    try:
        path = document_store.find_document(tailor_id, kind, format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=path.name, media_type=_MEDIA_TYPES[format])

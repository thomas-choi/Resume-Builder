"""REST + SSE routes for the resume builder API."""

import asyncio
import re
import uuid
from functools import partial
from pathlib import Path

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.agents.ingestion_graph import build_ingestion_graph
from src.agents.tailoring_graph import build_tailoring_graph
from src.api.deps import current_user
from src.models.schemas import CareerProfile, ReviewDecision, SourceDocument, User
from src.tools.docx_reader import read_docx
from src.tools.github_client import fetch_github_profile, free_text_source
from src.tools.linkedin_export import read_linkedin_export
from src.tools.pdf_reader import read_pdf
from src.utils import auth_store, document_store, profile_store, run_store
from src.utils.logging_setup import set_run_id, set_user

# Business routes — every one requires a resolved session (§14.8). The router
# carries the dependency so a route *added later* is protected unless someone
# deliberately opts it out, the opposite failure mode from decorating each
# handler. Handlers that need the email re-declare `Depends(current_user)`;
# FastAPI caches it within the request, so it resolves once.
router = APIRouter(dependencies=[Depends(current_user)])

# Unauthenticated liveness check — the only business-router sibling that must be
# reachable without a session. `/auth/*` lives on its own router (auth_routes).
public_router = APIRouter()

_DONE = {"event": "done"}

_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


class JobRegistry:
    """In-process registry of per-job asyncio queues for SSE progress.

    Each entry also records an **owner** (the `uid`, §14.8): the SSE stream is
    an in-process side channel that no path check would cover, so a subscriber
    for another account's ``job_id`` must be turned away rather than allowed to
    watch its node-by-node progress.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._owners: dict[str, str] = {}

    def create(self, job_id: str, owner: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[job_id] = queue
        self._owners[job_id] = owner
        return queue

    def get(self, job_id: str) -> asyncio.Queue | None:
        return self._queues.get(job_id)

    def owner(self, job_id: str) -> str | None:
        return self._owners.get(job_id)

    def discard(self, job_id: str) -> None:
        self._queues.pop(job_id, None)
        self._owners.pop(job_id, None)


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


def _load_upload(
    email: str, run_id: str, upload: UploadFile
) -> tuple[SourceDocument, dict]:
    """Archive an uploaded CV under the run's sources dir, then parse it.

    The raw bytes are persisted under ``<user root>/sources/{run_id}/cv/`` *before*
    parsing, so an upload survives even if the graph later fails and the run can
    still be reconstructed. Returns the parsed source document and its manifest
    entry.
    """
    filename = upload.filename or "cv"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".docx", ".pdf"):
        raise HTTPException(400, f"unsupported CV file type: {suffix or '(none)'}")
    data = upload.file.read()
    stored = run_store.save_source_file(email, run_id, "cv", filename, data)
    doc = read_docx(stored) if suffix == ".docx" else read_pdf(stored)
    # The *stored* name, not the uploaded one: two uploads called `CV.docx` are
    # archived as `CV.docx` / `CV-2.docx`, and their source ids must stay as
    # distinct as their files or raw_source_map traceability collapses.
    doc.id = f"{doc.source_type}:{stored.name}"
    doc.stored_path = str(stored)
    return doc, run_store.source_entry("cv", stored, data, source_id=doc.id)


def _load_linkedin_export(
    email: str, run_id: str, upload: UploadFile
) -> tuple[SourceDocument, dict]:
    """Archive an uploaded LinkedIn data export, then parse it.

    Same order as :func:`_load_upload`: the raw archive is persisted under
    ``<user root>/sources/{run_id}/linkedin/`` *before* parsing, so a rejected
    export is still on disk to inspect. Parsing is deterministic (no LLM) and
    never scrapes LinkedIn — the person's own export is the only supported input.
    """
    filename = upload.filename or "linkedin-export.zip"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".zip", ".csv"):
        raise HTTPException(
            400, f"unsupported LinkedIn export file type: {suffix or '(none)'}"
        )
    data = upload.file.read()
    stored = run_store.save_source_file(email, run_id, "linkedin", filename, data)
    try:
        doc = read_linkedin_export(stored)
    except ValueError as exc:
        raise HTTPException(400, f"unreadable LinkedIn export: {exc}") from exc
    # The stored name, so two same-named exports stay distinct (see _load_upload).
    doc.id = f"linkedin:{stored.name}"
    doc.stored_path = str(stored)
    return doc, run_store.source_entry("linkedin", stored, data, source_id=doc.id)


def _warning_text(error: dict) -> str:
    """One skipped item, as a single line for the SSE `warning` stream."""
    subject = error.get("repo") or error.get("source") or "source"
    return f"{subject}: {error.get('reason') or 'could not be extracted'}"


@public_router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.post("/ingest")
async def ingest(
    cv: list[UploadFile] | None = None,
    linkedin_export: list[UploadFile] | None = None,
    github_username: str | None = Form(default=None),
    github_token: str | None = Form(default=None),
    free_text: str | None = Form(default=None),
    job_id: str | None = Form(default=None),
    profile_id: str | None = Form(default=None),
    user: User = Depends(current_user),
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

    Extraction is partial-failure tolerant, and says so: anything it could not
    read (a specific GitHub repo, or a whole source) is listed in
    `source_errors` on the response and streamed as a `warning` SSE event. A
    GitHub source whose repos were dropped has its archived `github.json`
    rewritten to the repos that reached the profile, with the as-fetched
    document kept beside it as `github.raw.json`.

    `github_token` overrides the server's `GITHUB_TOKEN` for this request only.
    A token belonging to `github_username` also unlocks their private repos and
    private org memberships; anyone else's only raises rate limits. It is a
    secret in transit: never archived, never written to the manifest, never
    logged.
    """
    # Allocate the run/correlation id up front so raw inputs can be archived
    # before parsing (and before the graph runs). job_id doubles as run_id.
    email = user.email
    run_id = job_id or uuid.uuid4().hex[:12]
    set_run_id(run_id)
    set_user(auth_store.uid(email))
    if profile_id is not None:
        profile_id = _validate_profile_id(profile_id)
    # An empty form field is no token at all — fall back to config.GITHUB_TOKEN.
    github_token = (github_token or "").strip() or None

    sources: list[SourceDocument] = []
    manifest_entries: list[dict] = []
    for upload in cv or []:
        doc, entry = _load_upload(email, run_id, upload)
        sources.append(doc)
        manifest_entries.append(entry)
    for upload in linkedin_export or []:
        doc, entry = _load_linkedin_export(email, run_id, upload)
        sources.append(doc)
        manifest_entries.append(entry)
    if github_username:
        gh_doc = await anyio.to_thread.run_sync(
            partial(fetch_github_profile, github_username, token=github_token)
        )
        gh_bytes = gh_doc.model_dump_json(indent=2).encode("utf-8")
        gh_path = run_store.save_source_file(
            email, run_id, "github", "github.json", gh_bytes
        )
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
            email, run_id, "linkedin", "linkedin-summary.txt", ft_bytes
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

    run_store.write_manifest(email, run_id, manifest_entries)

    queue = jobs.create(run_id, auth_store.uid(email))
    loop = asyncio.get_running_loop()

    def publish(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_graph() -> dict:
        set_run_id(run_id)
        set_user(auth_store.uid(email))
        graph = build_ingestion_graph()
        state: dict = {}
        stream_input: dict = {"email": email, "run_id": run_id, "sources": sources}
        if profile_id:
            # store_profile threads this to save_profile: existing id → new
            # version, unknown id → created at v1.
            stream_input["profile_id"] = profile_id
        for update in graph.stream(stream_input, stream_mode="updates"):
            for node, node_state in update.items():
                publish({"event": "node", "data": node})
                # A source or repo that could not be extracted is reported as it
                # happens: a run that silently lost a whole source must never
                # again render as a clean success.
                for error in (node_state or {}).get("source_errors") or []:
                    publish({"event": "warning", "data": _warning_text(error)})
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
        # Partial success is still success, but it is never silent: every item
        # the extractor could not read is named here.
        "source_errors": state.get("source_errors") or [],
        "profile": profile.model_dump(),
    }


@router.get("/ingest/{job_id}/events")
async def ingest_events(
    job_id: str, user: User = Depends(current_user)
) -> EventSourceResponse:
    """SSE stream of per-node progress for an ingestion job.

    The stream is owned by the account that created the job: another user
    subscribing to it gets a ``404`` (never their progress), the same answer as
    a job that never existed. A subscribe-before-POST for one's *own* job (the
    documented client pattern) creates the entry owned by the subscriber.
    """
    caller = auth_store.uid(user.email)
    owner = jobs.owner(job_id)
    if owner is not None and owner != caller:
        raise HTTPException(404, f"no such job {job_id}")
    queue = jobs.get(job_id) or jobs.create(job_id, caller)

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
def get_profile(
    profile_id: str,
    version: int | None = None,
    user: User = Depends(current_user),
) -> dict:
    """Fetch a stored profile (latest version by default) under the caller's root.

    An id that belongs to another account simply does not exist under this
    account's root, so it returns the same ``404`` as one that never existed —
    never a ``403`` that would confirm the id is real (§14.8).
    """
    email = user.email
    profile_id = _validate_profile_id(profile_id)
    try:
        profile = profile_store.load_profile(email, profile_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "profile_id": profile_id,
        "version": version or profile_store.latest_version(email, profile_id),
        "versions": profile_store.list_versions(email, profile_id),
        "profile": profile.model_dump(),
    }


@router.put("/profile/{profile_id}")
def put_profile(
    profile_id: str,
    profile: CareerProfile,
    user: User = Depends(current_user),
) -> dict:
    """Save a user-edited profile as a new version (v1 conflict resolution).

    Targeting an id under another account's root is a ``404``: it does not
    exist here, and the write only ever lands in the caller's own tree.
    """
    email = user.email
    profile_id = _validate_profile_id(profile_id)
    if profile_store.latest_version(email, profile_id) == 0:
        raise HTTPException(404, f"profile {profile_id} not found")
    _, version = profile_store.save_profile(email, profile, profile_id)
    return {"profile_id": profile_id, "version": version}


class TailorRequest(BaseModel):
    profile_id: str
    job_post: str
    version: int | None = None
    render: bool = False
    cover_letter: bool = False
    approve_flagged: bool = False


def _thread(email: str, tailor_id: str) -> dict:
    """Checkpointer config for one tailoring run, namespaced to the owner (§14.8).

    The checkpointer key is ``f"{uid}:{tailor_id}"``, not the bare ``tailor_id``:
    a guessed id must not resume (or read) another account's paused human-review
    run. Every path that builds this config — tailor, review, resume — agrees on
    the namespaced key.
    """
    return {"configurable": {"thread_id": f"{auth_store.uid(email)}:{tailor_id}"}}


def _pending_review(state: dict) -> dict | None:
    """The review payload a paused graph is waiting on, or ``None``.

    LangGraph reports a pause by putting `Interrupt` objects on `__interrupt__`
    in the returned state; the payload is what `human_review` passed to
    `interrupt()` (a serialized `ReviewRequest`).
    """
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return None
    return interrupts[0].value


def _tailor_response(email: str, tailor_id: str, profile_id: str, state: dict) -> dict:
    """Build (and persist) the response shared by `/tailor` and `/resume`."""
    cover_letter = state.get("cover_letter")
    documents = state.get("documents") or []
    review_request = _pending_review(state)
    response = {
        "profile_id": profile_id,
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
        # Phase 4: the run is paused at the human-review checkpoint. Nothing is
        # rendered until POST /tailor/{tailor_id}/resume carries a decision.
        "review_required": review_request is not None,
        "review": review_request,
        "review_url": f"/tailor/{tailor_id}/review" if review_request else None,
    }
    # Persist the result next to the documents so a download can be traced back
    # to the claims the validation gate checked.
    document_store.save_result(
        email,
        tailor_id,
        {k: v for k, v in response.items() if k != "documents"},
    )
    return response


@router.post("/tailor")
async def tailor(request: TailorRequest, user: User = Depends(current_user)) -> dict:
    """Run the tailoring graph: job analysis -> tailoring -> validation -> render.

    Set `render` to also produce document files (`.docx`, plus `.pdf` when
    LibreOffice is available), downloadable from
    `GET /document/{tailor_id}`. Set `cover_letter` to generate one alongside
    the CV; it is returned in the response either way, and rendered too when
    `render` is set.

    Rendering is gated by the validation result. When flags exist and `render`
    was asked for, the graph **pauses** at its human-review checkpoint:
    `review_required` is true, `review` holds the flagged items, and nothing is
    written until `POST /tailor/{tailor_id}/resume` carries a decision.
    Pass `approve_flagged` to accept every flag up front and skip the pause.
    """
    email = user.email
    profile_id = _validate_profile_id(request.profile_id)
    try:
        profile = profile_store.load_profile(email, profile_id, request.version)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not request.job_post.strip():
        raise HTTPException(400, "job_post must not be empty")

    tailor_id = uuid.uuid4().hex[:12]
    set_user(auth_store.uid(email))

    def run_graph() -> dict:
        set_user(auth_store.uid(email))
        graph = build_tailoring_graph()
        return graph.invoke(
            {
                "email": email,
                "profile": profile,
                "job_post": request.job_post,
                "tailor_id": tailor_id,
                "render": request.render,
                "want_cover_letter": request.cover_letter,
                "approved": request.approve_flagged,
            },
            _thread(email, tailor_id),
        )

    state = await anyio.to_thread.run_sync(run_graph)
    return _tailor_response(email, tailor_id, profile_id, state)


def _checked_tailor_id(tailor_id: str) -> str:
    """Validate a path tailor_id (it addresses a directory) or raise 400."""
    try:
        return document_store.validate_tailor_id(tailor_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/tailor/{tailor_id}/review")
def get_review(tailor_id: str, user: User = Depends(current_user)) -> dict:
    """Fetch the flagged items a paused tailoring run is waiting on.

    `pending` distinguishes a run that is still paused — and can therefore be
    resumed — from the archived record of a review that was already answered
    (or whose checkpoint was lost to a restart). Both the checkpointer key and
    the review record are namespaced to the caller, so another account's run
    resolves to a ``404``.

    Returns:
        The `ReviewRequest` payload plus `pending`.

    Raises:
        HTTPException: 404 when that run never paused for review (or belongs to
            someone else).
    """
    email = user.email
    tailor_id = _checked_tailor_id(tailor_id)
    snapshot = build_tailoring_graph().get_state(_thread(email, tailor_id))
    interrupts = getattr(snapshot, "interrupts", ()) or ()
    if interrupts:
        return {"pending": True, **interrupts[0].value}
    stored = document_store.load_review(email, tailor_id)
    if stored is None:
        raise HTTPException(404, f"no review pending for tailor {tailor_id}")
    return {"pending": False, **stored}


@router.post("/tailor/{tailor_id}/resume")
async def resume_tailor(
    tailor_id: str, decision: ReviewDecision, user: User = Depends(current_user)
) -> dict:
    """Resume a paused run with the human's per-item decision.

    Items left out of `approvals` (and not covered by `approve_all`) are
    **removed** from the CV — silence is not consent for a claim the gate could
    not trace. The run then continues to the cover letter and rendering.

    The checkpointer key is namespaced to the caller, so a guessed `tailor_id`
    resolves to *this* account's threads only: another account's paused run
    cannot be resumed here (it presents as ``404`` — no review pending).

    Raises:
        HTTPException: 404 when no review is pending for that run — it was
            already resumed, never paused, belongs to someone else, or its
            checkpoint was lost to a restart (the checkpointer is in-process).
    """
    email = user.email
    tailor_id = _checked_tailor_id(tailor_id)
    set_user(auth_store.uid(email))
    graph = build_tailoring_graph()
    snapshot = graph.get_state(_thread(email, tailor_id))
    if not (getattr(snapshot, "interrupts", ()) or ()):
        raise HTTPException(404, f"no review pending for tailor {tailor_id}")

    def run_graph() -> dict:
        set_user(auth_store.uid(email))
        return graph.invoke(
            Command(resume=decision.model_dump()), _thread(email, tailor_id)
        )

    state = await anyio.to_thread.run_sync(run_graph)
    stored = document_store.load_result(email, tailor_id) or {}
    return _tailor_response(email, tailor_id, stored.get("profile_id", ""), state)


@router.get("/document/{tailor_id}")
def get_document(
    tailor_id: str,
    kind: str = "cv",
    format: str = "docx",
    user: User = Depends(current_user),
) -> FileResponse:
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
        path = document_store.find_document(user.email, tailor_id, kind, format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        # A tailor_id under another account's root simply is not found here —
        # the same 404 as one that was never rendered (§14.8).
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=path.name, media_type=_MEDIA_TYPES[format])

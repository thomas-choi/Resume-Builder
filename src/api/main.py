"""FastAPI app factory. Also serves the built Phase 4 review UI."""

import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from src import config
from src.api.auth_routes import auth_router
from src.api.routes import public_router, router
from src.utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    setup_logging()
    logger.info(
        "Resume Builder API starting (LOG_LEVEL=%s, LOG_FILE=%s)",
        config.LOG_LEVEL,
        config.LOG_FILE or "console only",
    )
    app = FastAPI(title="Resume Builder", version="0.1.0")
    # Business routes require a session (§14.8); the public router carries only
    # /healthz, and the auth router carries /auth/* — both unauthenticated.
    app.include_router(router)
    app.include_router(public_router)
    # Passwordless account routes (Phase 7.b), unauthenticated. Included before
    # the "/" static mount so /auth/* wins over the SPA fallback.
    app.include_router(auth_router)

    # The review UI is one container with the API (design doc §10): a built
    # frontend is served from `/`, so the browser talks to same-origin paths and
    # no CORS or second service is needed. Mounted last, after every API route,
    # so `/ingest`, `/docs` etc. still win; absent (backend-only checkout, or
    # before `npm run build`) `/` falls back to the API docs.
    index = config.FRONTEND_DIR / "index.html"
    if index.is_file():
        logger.info("Serving the review UI from %s", config.FRONTEND_DIR)
        app.mount(
            "/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="ui"
        )
    else:
        logger.info(
            "No built frontend at %s — '/' redirects to the API docs",
            config.FRONTEND_DIR,
        )

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            """Browser-friendly landing: redirect to the interactive API docs."""
            return RedirectResponse("/docs")

    return app


app = create_app()

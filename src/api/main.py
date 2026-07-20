"""FastAPI app factory. Phase 4 will also serve the built frontend from here."""

import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from src import config
from src.api.routes import router
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
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        """Browser-friendly landing: redirect to the interactive API docs."""
        return RedirectResponse("/docs")

    return app


app = create_app()

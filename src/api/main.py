"""FastAPI app factory. Phase 4 will also serve the built frontend from here."""

from fastapi import FastAPI

from src.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Resume Builder", version="0.1.0")
    app.include_router(router)
    return app


app = create_app()

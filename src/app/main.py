from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.dashboard import router as dashboard_router
from app.api.findings import router as findings_router
from app.api.resources import router as resources_router
from app.api.runs import router as runs_router
from app.auth import router as auth_router
from app.config import STATIC_DIR, STATIC_STYLES_DIR
from app.web import router as web_router


def create_app() -> FastAPI:
    app = FastAPI(title="Superset SQL Lab Platform")
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_STYLES_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(runs_router)
    app.include_router(findings_router)
    app.include_router(resources_router)
    app.include_router(web_router)
    return app


app = create_app()

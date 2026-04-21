from __future__ import annotations

from fastapi import FastAPI

from app.api.findings import router as findings_router
from app.api.resources import router as resources_router
from app.api.runs import router as runs_router
from app.web import router as web_router


def create_app() -> FastAPI:
    app = FastAPI(title="Superset SQL Lab Platform")
    app.include_router(runs_router)
    app.include_router(findings_router)
    app.include_router(resources_router)
    app.include_router(web_router)
    return app


app = create_app()

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import Session

from app.db import engine
from app.services.dashboard import build_overview_summary


router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/overview")
def get_dashboard_overview() -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        summary = build_overview_summary(session)
        return {"data": summary.to_dict()}

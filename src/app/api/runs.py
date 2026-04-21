from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session

from app.db import engine
from app.services.runs import create_and_store_run, get_run_detail


router = APIRouter()


class RunCreateRequest(BaseModel):
    job_definition_id: int
    step_types: list[str]


@router.post("/runs")
def create_run(request: RunCreateRequest) -> dict:
    with Session(engine) as session:
        run = create_and_store_run(session, request.job_definition_id, request.step_types)
        return get_run_detail(session, run.id)

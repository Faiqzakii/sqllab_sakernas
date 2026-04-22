from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import require_admin_session
from app.db import engine

from app.engine.superset_executor import build_superset_executor
from app.models import DatasetSnapshot, JobDefinition, RuleDefinition
from app.services.jobs import execute_job_definition


router = APIRouter()


def _latest_job_definition(session: Session) -> JobDefinition | None:
    jobs = session.exec(select(JobDefinition)).all()
    if not jobs:
        return None
    return max(jobs, key=lambda job: job.id or 0)


class JobDefinitionCreateRequest(BaseModel):
    name: str
    execution_mode: str
    sql_template: str | None = None
    params_schema_json: dict = {}
    merge_key_columns_json: list[str] = []
    identity_columns_json: list[str] = []


class RuleDefinitionCreateRequest(BaseModel):
    name: str
    kind: str
    severity_default: str
    identity_columns_required_json: list[str] = []


class SnapshotCreateRequest(BaseModel):
    run_id: int
    row_count: int
    artifact_path: str


class JobDefinitionUpdateRequest(BaseModel):
    name: str | None = None
    execution_mode: str | None = None
    sql_template: str | None = None
    params_schema_json: dict | None = None
    merge_key_columns_json: list[str] | None = None
    identity_columns_json: list[str] | None = None


@router.post("/job-definitions", dependencies=[Depends(require_admin_session)])
def create_job_definition(request: JobDefinitionCreateRequest) -> dict:
    with Session(engine) as session:
        job = JobDefinition(**request.model_dump())
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.model_dump()


@router.get("/job-definitions", dependencies=[Depends(require_admin_session)])
def list_job_definitions() -> list[dict]:
    with Session(engine) as session:
        jobs = session.exec(select(JobDefinition)).all()
        return [job.model_dump() for job in jobs]


@router.post("/job-definitions/{job_definition_id}/execute", dependencies=[Depends(require_admin_session)])
def execute_job(job_definition_id: int, debug: bool = Query(default=False)) -> dict:
    with Session(engine) as session:
        job = session.get(JobDefinition, job_definition_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "job_not_found", "message": "Job definition not found"}})
        source_data_path = job.params_schema_json.get("source_data_path")
        resolved_path = Path(source_data_path) if isinstance(source_data_path, str) else None
        superset_executor = build_superset_executor(job)
        try:
            return execute_job_definition(
                session,
                job_definition_id,
                source_data_path=resolved_path,
                superset_executor=superset_executor,
            )
        except Exception as exc:
            if debug:
                raise HTTPException(status_code=500, detail={"error": str(exc), "type": type(exc).__name__})
            raise


@router.post("/rule-definitions", dependencies=[Depends(require_admin_session)])
def create_rule_definition(request: RuleDefinitionCreateRequest) -> dict:
    with Session(engine) as session:
        rule = RuleDefinition(**request.model_dump())
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.model_dump()


@router.get("/rule-definitions", dependencies=[Depends(require_admin_session)])
def list_rule_definitions() -> list[dict]:
    with Session(engine) as session:
        rules = session.exec(select(RuleDefinition)).all()
        return [rule.model_dump() for rule in rules]


@router.post("/snapshots", dependencies=[Depends(require_admin_session)])
def create_snapshot(request: SnapshotCreateRequest) -> dict:
    with Session(engine) as session:
        snapshot = DatasetSnapshot(**request.model_dump())
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot.model_dump()


@router.get("/snapshots", dependencies=[Depends(require_admin_session)])
def list_snapshots() -> list[dict]:
    with Session(engine) as session:
        snapshots = session.exec(select(DatasetSnapshot)).all()
        return [snapshot.model_dump() for snapshot in snapshots]


@router.get("/api/v1/config/job-definition", dependencies=[Depends(require_admin_session)])
def get_job_definition_configuration() -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        job = _latest_job_definition(session)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "job_not_found", "message": "No job definition configured"}})
        return {"data": job.model_dump()}


@router.patch("/api/v1/config/job-definition", dependencies=[Depends(require_admin_session)])
def update_job_definition_configuration(request: JobDefinitionUpdateRequest) -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        job = _latest_job_definition(session)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "job_not_found", "message": "No job definition configured"}})

        payload = request.model_dump(exclude_none=True)
        for field_name, value in payload.items():
            setattr(job, field_name, value)
        session.add(job)
        session.commit()
        session.refresh(job)
        return {
            "data": {
                "id": job.id,
                "updated": True,
                **job.model_dump(),
            }
        }

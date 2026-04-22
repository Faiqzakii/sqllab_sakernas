from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.auth import require_admin_session
from app.db import engine
from app.engine.superset_executor import build_superset_executor
from app.models import DatasetSnapshot, JobDefinition
from app.services.findings import trigger_anomaly_run
from app.services.jobs import execute_job_definition
from app.services.runs import create_and_store_run, get_run_detail, latest_runs_summary, list_runs


router = APIRouter()


class RunCreateRequest(BaseModel):
    job_definition_id: int
    step_types: list[str]


class ExtractionRunRequest(BaseModel):
    job_definition_id: int
    debug: bool = False


class AnomalyRunRequest(BaseModel):
    dataset_snapshot_id: int


@router.post("/runs", dependencies=[Depends(require_admin_session)])
def create_run(request: RunCreateRequest) -> dict:
    with Session(engine) as session:
        run = create_and_store_run(session, request.job_definition_id, request.step_types)
        return get_run_detail(session, run.id)


@router.post("/api/v1/run-control/extraction", dependencies=[Depends(require_admin_session)], status_code=202)
def trigger_extraction_run(request: ExtractionRunRequest) -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        job = session.get(JobDefinition, request.job_definition_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "job_not_found", "message": "Job definition not found"}})
        source_data_path = job.params_schema_json.get("source_data_path")
        resolved_path = Path(source_data_path) if isinstance(source_data_path, str) else None
        superset_executor = build_superset_executor(job)
        try:
            execution = execute_job_definition(
                session,
                request.job_definition_id,
                source_data_path=resolved_path,
                superset_executor=superset_executor,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": "extraction_failed", "message": str(exc)}},
            ) from exc
        detail = get_run_detail(session, execution["run_id"])
        return {
            "data": {
                "run_type": "extraction",
                "run_id": execution["run_id"],
                "status": detail["status"],
            }
        }


@router.post("/api/v1/run-control/anomaly", dependencies=[Depends(require_admin_session)], status_code=202)
def trigger_anomaly_findings_run(request: AnomalyRunRequest) -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        snapshot = session.get(DatasetSnapshot, request.dataset_snapshot_id)
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "snapshot_not_found", "message": "Dataset snapshot not found"}},
            )
        try:
            detail = trigger_anomaly_run(session, request.dataset_snapshot_id)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": "anomaly_run_failed", "message": str(exc)}},
            ) from exc
        return {
            "data": {
                "run_type": "anomaly",
                "run_id": detail["run_id"],
                "status": detail["status"],
            }
        }


@router.get("/api/v1/run-control/latest", dependencies=[Depends(require_admin_session)])
def get_latest_run_control_summary() -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        return {"data": latest_runs_summary(session)}


@router.get("/api/v1/run-control/runs", dependencies=[Depends(require_admin_session)])
def list_run_control_history(run_type: str | None = None, page: int = 1, per_page: int = 20) -> dict[str, object]:
    with Session(engine) as session:
        return list_runs(session, run_type=run_type, page=page, per_page=per_page)


@router.get("/api/v1/run-control/runs/{run_id}", dependencies=[Depends(require_admin_session)])
def get_run_control_detail(run_id: int) -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        try:
            detail = get_run_detail(session, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": {"code": "run_not_found", "message": str(exc)}}) from exc
        return {"data": detail}

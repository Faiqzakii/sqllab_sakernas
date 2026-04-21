from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import engine
from pathlib import Path

from app.engine.superset_auth import SupersetAuthBootstrap
from app.engine.superset_client import SupersetClient
from app.engine.superset_ui_runner import SupersetUiRunner
from app.models import DatasetSnapshot, JobDefinition, RuleDefinition
from app.services.jobs import execute_job_definition


router = APIRouter()


def build_superset_executor(job: JobDefinition):
    if job.execution_mode != "superset_sql":
        return None

    params = job.params_schema_json if isinstance(job.params_schema_json, dict) else {}
    base_url = params.get("base_url")
    sql_lab_url = params.get("sql_lab_url")
    if not isinstance(base_url, str) or not isinstance(sql_lab_url, str):
        return None

    auth = SupersetAuthBootstrap(base_url=base_url, sql_lab_url=sql_lab_url)
    auth_result = auth.login_and_capture()
    session = auth.build_requests_session(auth_result.cookies)
    ui_runner = SupersetUiRunner(
        sql_lab_url=sql_lab_url,
        auth_cookies=auth_result.cookies,
        browser=auth_result.browser,
        context=auth_result.context,
        page=auth_result.page,
    )
    client = SupersetClient(session=session, base_url=base_url, ui_runner=ui_runner)
    return client


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


@router.post("/job-definitions")
def create_job_definition(request: JobDefinitionCreateRequest) -> dict:
    with Session(engine) as session:
        job = JobDefinition(**request.model_dump())
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.model_dump()


@router.get("/job-definitions")
def list_job_definitions() -> list[dict]:
    with Session(engine) as session:
        jobs = session.exec(select(JobDefinition)).all()
        return [job.model_dump() for job in jobs]


@router.post("/job-definitions/{job_definition_id}/execute")
def execute_job(job_definition_id: int, debug: bool = Query(default=False)) -> dict:
    with Session(engine) as session:
        job = session.get(JobDefinition, job_definition_id)
        if job is None:
            raise ValueError(f"Job definition not found: {job_definition_id}")
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


@router.post("/rule-definitions")
def create_rule_definition(request: RuleDefinitionCreateRequest) -> dict:
    with Session(engine) as session:
        rule = RuleDefinition(**request.model_dump())
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return rule.model_dump()


@router.get("/rule-definitions")
def list_rule_definitions() -> list[dict]:
    with Session(engine) as session:
        rules = session.exec(select(RuleDefinition)).all()
        return [rule.model_dump() for rule in rules]


@router.post("/snapshots")
def create_snapshot(request: SnapshotCreateRequest) -> dict:
    with Session(engine) as session:
        snapshot = DatasetSnapshot(**request.model_dump())
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot.model_dump()


@router.get("/snapshots")
def list_snapshots() -> list[dict]:
    with Session(engine) as session:
        snapshots = session.exec(select(DatasetSnapshot)).all()
        return [snapshot.model_dump() for snapshot in snapshots]

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from sqlmodel import Session, select

from app.models import DatasetSnapshot, Run, RunStepExecution


@dataclass
class PlannedRunStep:
    step_type: str
    status: str = "pending"


@dataclass
class PlannedRun:
    job_definition_id: int
    status: str
    steps: list[PlannedRunStep]


def create_run_with_steps(job_definition_id: int, step_types: list[str]) -> PlannedRun:
    return PlannedRun(
        job_definition_id=job_definition_id,
        status="pending",
        steps=[PlannedRunStep(step_type=step_type) for step_type in step_types],
    )


def create_and_store_run(session: Session, job_definition_id: int, step_types: list[str]) -> Run:
    run = Run(job_definition_id=job_definition_id, status="pending", created_at=datetime.now(UTC))
    session.add(run)
    session.commit()
    session.refresh(run)

    if run.id is None:
        raise RuntimeError("Run ID was not generated")

    for step_type in step_types:
        session.add(RunStepExecution(run_id=run.id, step_type=step_type, status="pending"))

    session.commit()
    session.refresh(run)
    return run


def list_run_steps(session: Session, run_id: int) -> list[RunStepExecution]:
    return list(session.exec(select(RunStepExecution).where(RunStepExecution.run_id == run_id)).all())


def infer_run_type(steps: list[RunStepExecution]) -> str:
    step_names = {step.step_type for step in steps}
    if any(step.startswith("anomaly") or step.startswith("finding") for step in step_names):
        return "anomaly"
    if "snapshot_merge" in step_names or any(step.startswith("superset_sql") for step in step_names):
        return "extraction"
    return "extraction"


def _serialize_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _build_outputs(session: Session, run: Run) -> dict[str, object]:
    snapshot = session.exec(select(DatasetSnapshot).where(DatasetSnapshot.run_id == run.id)).first()
    steps = list_run_steps(session, run.id)
    run_type = infer_run_type(steps)

    if run_type == "anomaly":
        latest_snapshot = session.exec(select(DatasetSnapshot)).all()
        snapshot_id = None
        row_count = None
        if latest_snapshot:
            chosen_snapshot = max(
                latest_snapshot,
                key=lambda item: (item.created_at or datetime.min, item.id or 0),
            )
            snapshot_id = chosen_snapshot.id
            row_count = chosen_snapshot.row_count
        return {
            "dataset_snapshot_id": snapshot_id,
            "row_count": row_count,
        }

    if snapshot is None:
        return {}

    outputs: dict[str, object] = {
        "row_count": snapshot.row_count,
        "artifact_path": snapshot.artifact_path,
        "duckdb_artifact_path": snapshot.duckdb_artifact_path,
    }
    batch_debug_path = f"artifacts/snapshots/{run.id}/batches.json"
    if batch_debug_path:
        outputs["batch_debug_path"] = batch_debug_path
    return outputs


def get_run_detail(session: Session, run_id: int) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")

    steps = list_run_steps(session, run_id)
    return {
        "id": run.id,
        "run_id": run.id,
        "run_type": infer_run_type(steps),
        "job_definition_id": run.job_definition_id,
        "status": run.status,
        "created_at": _serialize_timestamp(run.created_at),
        "started_at": _serialize_timestamp(run.started_at),
        "completed_at": _serialize_timestamp(run.completed_at),
        "failed_at": _serialize_timestamp(run.failed_at),
        "steps": [
            {"id": step.id, "step_type": step.step_type, "status": step.status}
            for step in steps
        ],
        "outputs": _build_outputs(session, run),
    }


def list_runs(session: Session, run_type: str | None = None, page: int = 1, per_page: int = 20) -> dict[str, object]:
    runs = session.exec(select(Run)).all()
    entries: list[dict[str, object]] = []
    for run in runs:
        if run.id is None:
            continue
        steps = list_run_steps(session, run.id)
        resolved_run_type = infer_run_type(steps)
        if run_type and resolved_run_type != run_type:
            continue
        entries.append(
            {
                "run_id": run.id,
                "run_type": resolved_run_type,
                "status": run.status,
                "created_at": _serialize_timestamp(run.created_at),
                "completed_at": _serialize_timestamp(run.completed_at),
            }
        )

    entries.sort(key=lambda item: ((item["created_at"] or ""), item["run_id"]), reverse=True)
    total = len(entries)
    total_pages = max(1, ceil(total / per_page)) if total else 1
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": entries[start:end],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        },
    }


def latest_runs_summary(session: Session) -> dict[str, object]:
    latest: dict[str, dict[str, object] | None] = {"extraction": None, "anomaly": None}
    run_list = list_runs(session, page=1, per_page=200)["data"]
    for entry in run_list:
        run_type = str(entry["run_type"])
        if latest[run_type] is None:
            latest[run_type] = {
                "run_id": entry["run_id"],
                "status": entry["status"],
                "completed_at": entry["completed_at"],
            }
    return latest


def mark_run_completed(session: Session, run_id: int, status: str = "completed") -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    steps = list_run_steps(session, run_id)
    now = datetime.now(UTC)
    run.started_at = run.started_at or now
    if status == "completed":
        run.status = "completed"
        run.completed_at = now
    else:
        run.status = status
        run.failed_at = now
    session.add(run)
    for step in steps:
        step.status = "completed" if status == "completed" else status
        session.add(step)
    session.commit()
    session.refresh(run)
    return run

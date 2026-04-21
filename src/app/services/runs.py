from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models import Run, RunStepExecution


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


def get_run_detail(session: Session, run_id: int) -> dict:
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")

    steps = session.exec(select(RunStepExecution).where(RunStepExecution.run_id == run_id)).all()
    return {
        "id": run.id,
        "job_definition_id": run.job_definition_id,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "failed_at": run.failed_at.isoformat() if run.failed_at else None,
        "steps": [
            {"id": step.id, "step_type": step.step_type, "status": step.status}
            for step in steps
        ],
    }

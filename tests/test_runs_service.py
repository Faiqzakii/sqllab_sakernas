from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Run
from app.services.runs import create_and_store_run, create_run_with_steps


def test_create_run_with_steps_builds_pending_run_plan() -> None:
    run = create_run_with_steps(
        job_definition_id=1,
        step_types=["superset_sql", "local_python"],
    )

    assert run.status == "pending"
    assert [step.step_type for step in run.steps] == ["superset_sql", "local_python"]
    assert all(step.status == "pending" for step in run.steps)


def test_create_and_store_run_persists_created_timestamp() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        run = create_and_store_run(session, job_definition_id=7, step_types=["superset_sql", "snapshot_merge"])
        persisted = session.exec(select(Run).where(Run.id == run.id)).one()

    assert persisted.status == "pending"
    assert isinstance(persisted.created_at, datetime)
    assert persisted.started_at is None
    assert persisted.completed_at is None
    assert persisted.failed_at is None

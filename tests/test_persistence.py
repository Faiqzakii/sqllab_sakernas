from sqlmodel import Session, SQLModel, create_engine, select

from app.models import FindingRuleHit, Run
from app.services.findings import list_identity_findings, set_review_state_for_identity
from app.services.runs import create_and_store_run
from app.services.runs import get_run_detail


def test_create_and_store_run_persists_run_and_steps() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        run = create_and_store_run(
            session,
            job_definition_id=10,
            step_types=["superset_sql", "local_python"],
        )
        persisted_runs = session.exec(select(Run)).all()

    assert run.id is not None
    assert len(persisted_runs) == 1
    assert persisted_runs[0].job_definition_id == 10


def test_get_run_detail_returns_persisted_steps() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        run = create_and_store_run(
            session,
            job_definition_id=10,
            step_types=["superset_sql", "local_python"],
        )
        detail = get_run_detail(session, run.id)

    assert detail["id"] == run.id
    assert [step["step_type"] for step in detail["steps"]] == ["superset_sql", "local_python"]


def test_list_identity_findings_reads_grouped_results_from_database() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=1,
                rule_execution_id=1,
                identity_key="id-001",
                severity="warn",
                message="missing field",
                identity_payload_json={"identity_key": "id-001", "household_number": "12"},
            )
        )
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=2,
                rule_execution_id=2,
                identity_key="id-001",
                severity="critical",
                message="duplicate record",
                identity_payload_json={"identity_key": "id-001", "household_number": "12"},
            )
        )
        session.commit()

        aggregates = list_identity_findings(session)

    assert len(aggregates) == 1
    assert aggregates[0].identity_key == "id-001"
    assert aggregates[0].highest_severity == "critical"


def test_set_review_state_for_identity_persists_across_reads() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=1,
                rule_execution_id=1,
                identity_key="id-001",
                severity="warn",
                message="missing field",
                identity_payload_json={"identity_key": "id-001", "household_number": "12"},
            )
        )
        session.commit()

        set_review_state_for_identity(session, "id-001", "acknowledged")
        aggregates = list_identity_findings(session)

    assert aggregates[0].review_state == "acknowledged"

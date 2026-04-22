from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from app.models import DatasetSnapshot, FindingRuleHit, IdentityReviewState, Run, RunStepExecution
from app.services.dashboard import build_overview_summary


def test_build_overview_summary_returns_latest_dataset_and_findings_summary() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    extraction_completed_at = datetime(2026, 4, 21, 8, 42, tzinfo=UTC)
    anomaly_completed_at = datetime(2026, 4, 21, 9, 10, tzinfo=UTC)

    with Session(engine) as session:
        extraction_run = Run(job_definition_id=1, status="completed", created_at=extraction_completed_at, completed_at=extraction_completed_at)
        anomaly_run = Run(job_definition_id=1, status="completed", created_at=anomaly_completed_at, completed_at=anomaly_completed_at)
        session.add(extraction_run)
        session.add(anomaly_run)
        session.commit()
        session.refresh(extraction_run)
        session.refresh(anomaly_run)

        session.add(RunStepExecution(run_id=extraction_run.id, step_type="superset_sql:01", status="completed"))
        session.add(RunStepExecution(run_id=extraction_run.id, step_type="snapshot_merge", status="completed"))
        session.add(RunStepExecution(run_id=anomaly_run.id, step_type="anomaly_scan", status="completed"))
        session.add(RunStepExecution(run_id=anomaly_run.id, step_type="finding_aggregate", status="completed"))
        session.add(
            DatasetSnapshot(
                run_id=extraction_run.id,
                row_count=1400000,
                artifact_path="artifacts/snapshots/55/dataset.json",
                duckdb_artifact_path="data/dataset.duckdb",
                created_at=extraction_completed_at,
            )
        )
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=1,
                rule_execution_id=1,
                identity_key="id-001",
                severity="critical",
                message="duplicate record",
                identity_payload_json={"identity_key": "id-001"},
            )
        )
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=2,
                rule_execution_id=2,
                identity_key="id-002",
                severity="warn",
                message="missing value",
                identity_payload_json={"identity_key": "id-002"},
            )
        )
        session.add(IdentityReviewState(identity_key="id-002", review_state="reviewed"))
        session.commit()

        summary = build_overview_summary(session).to_dict()

    assert summary["latest_dataset"]["row_count"] == 1400000
    assert summary["latest_dataset"]["last_successful_update_at"] == extraction_completed_at.isoformat()
    assert summary["anomaly_query"]["last_run_at"] == anomaly_completed_at.isoformat()
    assert summary["findings_summary"]["total"] == 2
    assert summary["findings_summary"]["by_severity"] == {"critical": 1, "warn": 1}
    assert summary["findings_summary"]["by_review_state"] == {"open": 1, "reviewed": 1}

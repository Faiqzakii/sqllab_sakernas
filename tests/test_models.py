from app.models import (
    ConnectionProfile,
    DatasetSnapshot,
    FindingRuleHit,
    JobDefinition,
    RuleDefinition,
    RuleExecution,
    Run,
    RunStepExecution,
)
from app.sample_queries import SIMULATED_COMPLETE_DATA_BATCHING, SIMULATED_COMPLETE_DATA_SQL_TEMPLATE


def test_core_models_can_be_constructed() -> None:
    connection = ConnectionProfile(
        name="fasih",
        base_url="https://fasih-dashboard.bps.go.id",
        sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
        auth_mode="browser",
    )
    job = JobDefinition(
        name="job-1",
        execution_mode="superset_sql",
        sql_template=SIMULATED_COMPLETE_DATA_SQL_TEMPLATE,
        params_schema_json={"batching_strategy": SIMULATED_COMPLETE_DATA_BATCHING},
        merge_key_columns_json=["identity_key"],
        identity_columns_json=["identity_key", "household_number"],
    )
    run = Run(job_definition_id=1, status="pending")
    step = RunStepExecution(run_id=1, step_type="superset_sql", status="pending")
    snapshot = DatasetSnapshot(run_id=1, row_count=10, artifact_path="artifacts/snapshots/1/dataset.parquet")
    rule = RuleDefinition(
        name="missing-household",
        kind="python",
        severity_default="warn",
        identity_columns_required_json=["identity_key", "household_number"],
    )
    rule_execution = RuleExecution(rule_definition_id=1, dataset_snapshot_id=1, status="pending")
    hit = FindingRuleHit(
        dataset_snapshot_id=1,
        rule_definition_id=1,
        rule_execution_id=1,
        identity_key="abc",
        severity="warn",
        message="household number missing",
        identity_payload_json={"identity_key": "abc", "household_number": None},
    )

    assert connection.name == "fasih"
    assert job.execution_mode == "superset_sql"
    assert run.status == "pending"
    assert step.step_type == "superset_sql"
    assert snapshot.row_count == 10
    assert rule.kind == "python"
    assert rule_execution.status == "pending"
    assert hit.identity_key == "abc"

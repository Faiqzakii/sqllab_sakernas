import json
from pathlib import Path

import duckdb
import pandas as pd
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import JobDefinition, RunStepExecution
from app.sample_queries import SIMULATED_COMPLETE_DATA_BATCHING, SIMULATED_COMPLETE_DATA_SQL_TEMPLATE
from app.services.jobs import build_household_identity_key, execute_job_definition
from app.services.runs import get_run_detail


def test_build_household_identity_key_uses_expected_column_order() -> None:
    row = {
        "KODE_PROV": "65",
        "KODE_KAB": "71",
        "KODE_KEC": "030",
        "KODE_DESA": "004",
        "SLS": "0028",
        "SUBSLS": "00",
        "NKS": "20250434",
        "DSRT": 10,
        "NO_ART": "3",
    }

    identity_key = build_household_identity_key(row)

    assert identity_key == "6571030004002800-20250434-10-3"


def test_execute_job_definition_creates_run_and_snapshot_from_local_json_dataset(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    data_path = tmp_path / "superset_data.json"
    data_path.write_text(
        json.dumps(
            [
                {
                    "KODE_PROV": "65",
                    "KODE_KAB": "71",
                    "KODE_KEC": "030",
                    "KODE_DESA": "004",
                    "SLS": "0028",
                    "SUBSLS": "00",
                    "NKS": "20250434",
                    "DSRT": 10,
                    "NO_ART": "3",
                },
                {
                    "KODE_PROV": "65",
                    "KODE_KAB": "71",
                    "KODE_KEC": "030",
                    "KODE_DESA": "004",
                    "SLS": "0029",
                    "SUBSLS": "00",
                    "NKS": "20250435",
                    "DSRT": 14,
                    "NO_ART": "1",
                },
            ]
        ),
        encoding="utf-8",
    )

    with Session(engine) as session:
        job = JobDefinition(
            name="household-sync",
            execution_mode="superset_sql",
            sql_template=SIMULATED_COMPLETE_DATA_SQL_TEMPLATE,
            params_schema_json={"batching_strategy": SIMULATED_COMPLETE_DATA_BATCHING},
            merge_key_columns_json=["identity_key"],
            identity_columns_json=["identity_key", "household_number"],
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        execution = execute_job_definition(session, job.id, source_data_path=data_path)
        run_detail = get_run_detail(session, execution["run_id"])
        persisted_steps = session.exec(
            select(RunStepExecution).where(RunStepExecution.run_id == execution["run_id"])
        ).all()

    snapshot_artifact = Path(execution["artifact_path"])
    snapshot_rows = json.loads(snapshot_artifact.read_text(encoding="utf-8"))
    duckdb_artifact = Path(execution["duckdb_artifact_path"])

    assert execution["run_id"] is not None
    assert execution["snapshot_id"] is not None
    assert execution["row_count"] == 2
    assert snapshot_artifact.exists()
    assert duckdb_artifact.exists()
    assert snapshot_rows[0]["identity_key"] == "6571030004002800-20250434-10-3"
    with duckdb.connect(str(duckdb_artifact)) as connection:
        row_count = connection.execute("SELECT COUNT(*) FROM snapshot_data").fetchone()[0]
    assert row_count == 2
    assert run_detail["status"] == "completed"
    assert len(run_detail["steps"]) == 6
    assert [step["status"] for step in run_detail["steps"]] == ["completed"] * 6
    assert len(persisted_steps) == 6


def test_execute_job_definition_can_use_superset_executor_per_batch(tmp_path: Path) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    class FakeSupersetExecutor:
        def __init__(self) -> None:
            self.sql_calls: list[str] = []

        def run_query(self, sql: str):
            self.sql_calls.append(sql)
            if "art.level_2_code='01'" in sql:
                dataframe = pd.DataFrame([
                    {
                        "KODE_PROV": "65",
                        "KODE_KAB": "01",
                        "KODE_KEC": "030",
                        "KODE_DESA": "004",
                        "SLS": "0028",
                        "SUBSLS": "00",
                        "NKS": "20250434",
                        "DSRT": 10,
                        "NO_ART": "3",
                    }
                ])
            else:
                dataframe = pd.DataFrame()

            class Result:
                source = "backend"
                metadata = {"status_code": 200}

                def __init__(self, dataframe: pd.DataFrame) -> None:
                    self.dataframe = dataframe

            return Result(dataframe)

    with Session(engine) as session:
        job = JobDefinition(
            name="household-sync-live",
            execution_mode="superset_sql",
            sql_template=SIMULATED_COMPLETE_DATA_SQL_TEMPLATE,
            params_schema_json={"batching_strategy": SIMULATED_COMPLETE_DATA_BATCHING},
            merge_key_columns_json=["identity_key"],
            identity_columns_json=["identity_key", "household_number"],
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        executor = FakeSupersetExecutor()
        execution = execute_job_definition(session, job.id, superset_executor=executor)

    batch_debug_artifact = Path(execution["batch_debug_path"])
    batch_debug_rows = json.loads(batch_debug_artifact.read_text(encoding="utf-8"))
    duckdb_artifact = Path(execution["duckdb_artifact_path"])

    assert len(executor.sql_calls) == 5
    assert execution["row_count"] == 1
    assert duckdb_artifact.exists()
    assert batch_debug_artifact.exists()
    assert len(batch_debug_rows) == 5
    assert batch_debug_rows[0]["batch_params"] == {"level_2_code": "01"}

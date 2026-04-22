import json
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import DatasetSnapshot, JobDefinition, Run, RunStepExecution
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


def test_execute_job_definition_creates_run_and_snapshot_from_local_json_dataset(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.chdir(tmp_path)

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

    with Session(engine) as session:
        persisted_run = session.exec(select(Run).where(Run.id == execution["run_id"])).one()
        persisted_snapshot = session.exec(select(DatasetSnapshot).where(DatasetSnapshot.id == execution["snapshot_id"])).one()

    assert isinstance(persisted_run.created_at, datetime)
    assert isinstance(persisted_run.started_at, datetime)
    assert isinstance(persisted_run.completed_at, datetime)
    assert persisted_run.failed_at is None
    assert persisted_run.started_at >= persisted_run.created_at
    assert persisted_run.completed_at >= persisted_run.started_at
    assert isinstance(persisted_snapshot.created_at, datetime)
    assert persisted_snapshot.duckdb_artifact_path == str(duckdb_artifact)


def test_execute_job_definition_persists_failed_at_when_executor_raises() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    class FailingSupersetExecutor:
        def run_query(self, sql: str):
            raise RuntimeError("superset unavailable")

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

        with pytest.raises(RuntimeError, match="superset unavailable"):
            execute_job_definition(session, job.id, superset_executor=FailingSupersetExecutor())

        persisted_runs = session.exec(select(Run)).all()
        persisted_steps = session.exec(select(RunStepExecution)).all()
        persisted_snapshots = session.exec(select(DatasetSnapshot)).all()

    assert len(persisted_runs) == 1
    assert persisted_runs[0].status == "failed"
    assert isinstance(persisted_runs[0].created_at, datetime)
    assert isinstance(persisted_runs[0].started_at, datetime)
    assert persisted_runs[0].completed_at is None
    assert isinstance(persisted_runs[0].failed_at, datetime)
    assert persisted_runs[0].failed_at >= persisted_runs[0].started_at
    assert len(persisted_steps) == 6
    assert all(step.status == "failed" for step in persisted_steps)
    assert persisted_snapshots == []


def test_execute_job_definition_can_use_superset_executor_per_batch(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.chdir(tmp_path)

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


def test_execute_job_definition_closes_superset_executor_after_run(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.chdir(tmp_path)

    closed = {"value": False}

    class FakeSupersetExecutor:
        def run_query(self, sql: str):
            import pandas as pd

            class Result:
                source = "ui"
                metadata = {"row_count": 1}

                def __init__(self) -> None:
                    self.dataframe = pd.DataFrame(
                        [
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
                        ]
                    )

            return Result()

        def close(self) -> None:
            closed["value"] = True

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

        execute_job_definition(session, job.id, superset_executor=FakeSupersetExecutor())

    assert closed["value"] is True


def test_execute_job_definition_upserts_existing_duckdb_snapshot_by_identity_key(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.chdir(tmp_path)

    data_path = tmp_path / "superset_data.json"
    data_path.write_text(json.dumps({"rows": []}), encoding="utf-8")

    duckdb_path = tmp_path / "data" / "dataset.duckdb"
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(duckdb_path)) as connection:
        connection.execute(
            "CREATE TABLE snapshot_data AS SELECT * FROM (VALUES "
            "('65','71','030','004','0028','00','20250434',10,'3','6571030004002800-20250434-10-3'),"
            "('65','02','030','004','0029','00','20250435',14,'1','6502030004002900-20250435-14-1')"
            ") AS t(KODE_PROV,KODE_KAB,KODE_KEC,KODE_DESA,SLS,SUBSLS,NKS,DSRT,NO_ART,identity_key)"
        )

    class FakeSupersetExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def run_query(self, sql: str):
            self.calls += 1
            import pandas as pd

            if "art.level_2_code='01'" in sql:
                dataframe = pd.DataFrame(
                    [
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
                    ]
                )
            else:
                dataframe = pd.DataFrame()

            class Result:
                source = "ui"
                metadata = {"row_count": len(dataframe.index)}

                def __init__(self, dataframe: pd.DataFrame) -> None:
                    self.dataframe = dataframe

            return Result(dataframe)

        def close(self) -> None:
            return None

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

        execution = execute_job_definition(session, job.id, source_data_path=data_path, superset_executor=FakeSupersetExecutor())

    assert execution["incoming_row_count"] == 1
    assert execution["row_count"] == 3

    with duckdb.connect(str(duckdb_path)) as connection:
        rows = connection.execute(
            "SELECT KODE_KAB, identity_key FROM snapshot_data ORDER BY KODE_KAB"
        ).fetchall()

    assert rows == [
        ("01", "6501030004002800-20250434-10-3"),
        ("02", "6502030004002900-20250435-14-1"),
        ("71", "6571030004002800-20250434-10-3"),
    ]

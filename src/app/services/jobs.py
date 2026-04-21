from __future__ import annotations

import duckdb
import json
from pathlib import Path
from typing import Any

import pandas as pd
from sqlmodel import Session, select

from app.engine.merge_engine import merge_batches
from app.engine.query_planner import build_subquery_specs
from app.models import DatasetSnapshot, JobDefinition, Run, RunStepExecution
from app.services.runs import create_and_store_run


DEFAULT_SOURCE_DATA_PATH = Path(r"E:\Python Projects\Scraping Fasih\superset_sqllab_platform\data\superset_data.json")


def build_household_identity_key(row: dict[str, Any]) -> str:
    def pick(*names: str) -> Any:
        for name in names:
            if name in row:
                return row[name]
        raise KeyError(names[0])

    return (
        f"{pick('KODE_PROV', 'kode_prov')}{pick('KODE_KAB', 'kode_kab')}{pick('KODE_KEC', 'kode_kec')}"
        f"{pick('KODE_DESA', 'kode_desa')}{pick('SLS', 'sls')}{pick('SUBSLS', 'subsls')}"
        f"-{pick('NKS', 'nks')}-{pick('DSRT', 'dsrt')}-{pick('NO_ART', 'no_art')}"
    )


def _normalize_row_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}


def _load_source_rows(source_data_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(source_data_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [_normalize_row_keys(dict(row)) for row in payload]

    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [_normalize_row_keys(dict(row)) for row in rows]

    raise ValueError("Expected source dataset JSON to be a list of rows or an object containing a 'rows' list")


def _write_snapshot_artifact(run_id: int, rows: list[dict[str, Any]]) -> Path:
    artifact_path = Path("artifacts") / "snapshots" / str(run_id) / "dataset.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact_path


def _write_snapshot_duckdb_artifact(run_id: int, rows: list[dict[str, Any]]) -> Path:
    artifact_path = Path("data") / "dataset.duckdb"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    dataframe = pd.DataFrame(rows)
    with duckdb.connect(str(artifact_path)) as connection:
        connection.register("snapshot_rows", dataframe)
        connection.execute("CREATE OR REPLACE TABLE snapshot_data AS SELECT * FROM snapshot_rows")
        connection.unregister("snapshot_rows")

    return artifact_path


def _write_batch_debug_artifact(run_id: int, payload: list[dict[str, Any]]) -> Path:
    artifact_path = Path("artifacts") / "snapshots" / str(run_id) / "batches.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact_path


def _log_batch_event(run_id: int, payload: dict[str, Any]) -> Path:
    artifact_path = Path("artifacts") / "snapshots" / str(run_id) / "batches.log.jsonl"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return artifact_path


def _filter_rows_for_batch(rows: list[dict[str, Any]], batch_params: dict[str, Any]) -> list[dict[str, Any]]:
    level_2_code = batch_params.get("level_2_code")
    if level_2_code is None:
        return list(rows)
    return [row for row in rows if str(row.get("kode_kab")) == str(level_2_code)]


def execute_job_definition(
    session: Session,
    job_definition_id: int,
    source_data_path: Path | None = None,
    superset_executor: Any | None = None,
) -> dict:
    job = session.get(JobDefinition, job_definition_id)
    if job is None:
        raise ValueError(f"Job definition not found: {job_definition_id}")

    batching_strategy = job.params_schema_json.get("batching_strategy") if isinstance(job.params_schema_json, dict) else None
    if batching_strategy:
        batch_specs = build_subquery_specs(job.sql_template or "", {}, batching_strategy)
        step_types = [f"superset_sql:{spec.batch_params[batching_strategy['param']]}" for spec in batch_specs]
    else:
        batch_specs = []
        step_types = [job.execution_mode]
    step_types.append("snapshot_merge")

    run = create_and_store_run(session, job_definition_id, step_types)

    run_steps = list(session.exec(select(RunStepExecution).where(RunStepExecution.run_id == run.id)).all())

    resolved_source_path = (source_data_path or DEFAULT_SOURCE_DATA_PATH).resolve()
    rows = _load_source_rows(resolved_source_path)
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized_row = dict(row)
        normalized_row["identity_key"] = build_household_identity_key(row)
        normalized_rows.append(normalized_row)

    batch_dataframes: list[pd.DataFrame] = []
    batch_reports: list[dict[str, Any]] = []
    if batch_specs:
        for step, spec in zip(run_steps[:-1], batch_specs, strict=False):
            _log_batch_event(
                run.id,
                {
                    "stage": "batch.start",
                    "run_id": run.id,
                    "step_id": step.id,
                    "step_type": step.step_type,
                    "batch_order": spec.batch_order,
                    "batch_params": spec.batch_params,
                },
            )
            if superset_executor is not None:
                _log_batch_event(
                    run.id,
                    {
                        "stage": "query.submit",
                        "run_id": run.id,
                        "step_id": step.id,
                        "step_type": step.step_type,
                        "batch_order": spec.batch_order,
                        "rendered_sql": spec.rendered_sql,
                    },
                )
                query_result = superset_executor.run_query(spec.rendered_sql)
                batch_reports.append(
                    {
                        "step_id": step.id,
                        "step_type": step.step_type,
                        "batch_order": spec.batch_order,
                        "batch_params": spec.batch_params,
                        "rendered_sql": spec.rendered_sql,
                        "source": query_result.source,
                        "row_count": len(query_result.dataframe.index),
                    }
                )
                _write_batch_debug_artifact(run.id, batch_reports)
                _log_batch_event(
                    run.id,
                    {
                        "stage": "query.complete",
                        "run_id": run.id,
                        "step_id": step.id,
                        "step_type": step.step_type,
                        "batch_order": spec.batch_order,
                        "source": query_result.source,
                        "row_count": len(query_result.dataframe.index),
                    },
                )
                if not query_result.dataframe.empty:
                    batch_dataframe = query_result.dataframe.copy()
                    batch_dataframe["identity_key"] = batch_dataframe.apply(
                        lambda row: build_household_identity_key(row.to_dict()),
                        axis=1,
                    )
                    batch_dataframes.append(batch_dataframe)
            else:
                filtered_rows = _filter_rows_for_batch(normalized_rows, spec.batch_params)
                batch_reports.append(
                    {
                        "step_id": step.id,
                        "step_type": step.step_type,
                        "batch_order": spec.batch_order,
                        "batch_params": spec.batch_params,
                        "rendered_sql": spec.rendered_sql,
                        "source": "local_json",
                        "row_count": len(filtered_rows),
                    }
                )
                _write_batch_debug_artifact(run.id, batch_reports)
                _log_batch_event(
                    run.id,
                    {
                        "stage": "query.complete",
                        "run_id": run.id,
                        "step_id": step.id,
                        "step_type": step.step_type,
                        "batch_order": spec.batch_order,
                        "source": "local_json",
                        "row_count": len(filtered_rows),
                    },
                )
                if filtered_rows:
                    batch_dataframes.append(pd.DataFrame(filtered_rows))
            step.status = "completed"
            session.add(step)
            _log_batch_event(
                run.id,
                {
                    "stage": "batch.complete",
                    "run_id": run.id,
                    "step_id": step.id,
                    "step_type": step.step_type,
                    "batch_order": spec.batch_order,
                },
            )
    else:
        batch_dataframes.append(pd.DataFrame(normalized_rows))
        run_steps[0].status = "completed"
        session.add(run_steps[0])

    if batch_dataframes:
        merged_dataframe = merge_batches(batch_dataframes, merge_keys=job.merge_key_columns_json)
    else:
        merged_dataframe = pd.DataFrame(columns=job.merge_key_columns_json)
    merged_rows = merged_dataframe.to_dict(orient="records")

    run_steps[-1].status = "completed"
    session.add(run_steps[-1])

    run.status = "completed"
    session.add(run)

    artifact_path = _write_snapshot_artifact(run.id, merged_rows)
    duckdb_artifact_path = _write_snapshot_duckdb_artifact(run.id, merged_rows)
    batch_debug_path = _write_batch_debug_artifact(run.id, batch_reports)
    _log_batch_event(
        run.id,
        {
            "stage": "artifact.write",
            "run_id": run.id,
            "snapshot_path": str(artifact_path),
            "row_count": len(merged_rows),
        },
    )
    snapshot = DatasetSnapshot(run_id=run.id, row_count=len(merged_rows), artifact_path=str(artifact_path))
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)

    return {
        "job_definition_id": job_definition_id,
        "run_id": run.id,
        "snapshot_id": snapshot.id,
        "row_count": snapshot.row_count,
        "artifact_path": snapshot.artifact_path,
        "duckdb_artifact_path": str(duckdb_artifact_path),
        "batch_debug_path": str(batch_debug_path),
    }

import sqlite3
from pathlib import Path

from app.main import create_app


def test_schema_upgrade_adds_timestamp_columns_to_existing_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE run (id INTEGER PRIMARY KEY, job_definition_id INTEGER NOT NULL, status TEXT NOT NULL)")
    connection.execute("CREATE TABLE datasetsnapshot (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, row_count INTEGER NOT NULL, artifact_path TEXT NOT NULL)")
    connection.commit()
    connection.close()

    from app.db import create_db_and_tables

    create_db_and_tables(f"sqlite:///{database_path}")

    connection = sqlite3.connect(database_path)
    try:
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info('run')")}
        snapshot_columns = {row[1] for row in connection.execute("PRAGMA table_info('datasetsnapshot')")}
    finally:
        connection.close()

    assert {"created_at", "started_at", "completed_at", "failed_at"}.issubset(run_columns)
    assert {"created_at", "duckdb_artifact_path"}.issubset(snapshot_columns)


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()

    assert app.title == "Superset SQL Lab Platform"

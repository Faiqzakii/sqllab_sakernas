from __future__ import annotations

from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

from app.config import DATA_DIR, SQLITE_PATH

# Import models before create_all so SQLModel metadata is populated.
from app import models  # noqa: F401


DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False)


def _sqlite_column_names(target_engine, table_name: str) -> set[str]:
    with target_engine.connect() as connection:
        rows = connection.exec_driver_sql(f"PRAGMA table_info('{table_name}')").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_sqlite_column(target_engine, table_name: str, column_name: str, ddl: str) -> None:
    existing_columns = _sqlite_column_names(target_engine, table_name)
    if column_name in existing_columns:
        return
    with target_engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def _upgrade_sqlite_schema(target_engine) -> None:
    _ensure_sqlite_column(target_engine, "run", "created_at", "TIMESTAMP")
    _ensure_sqlite_column(target_engine, "run", "started_at", "TIMESTAMP")
    _ensure_sqlite_column(target_engine, "run", "completed_at", "TIMESTAMP")
    _ensure_sqlite_column(target_engine, "run", "failed_at", "TIMESTAMP")
    _ensure_sqlite_column(target_engine, "datasetsnapshot", "duckdb_artifact_path", "TEXT")
    _ensure_sqlite_column(target_engine, "datasetsnapshot", "created_at", "TIMESTAMP")


def create_db_and_tables(database_url: str | None = None) -> None:
    target_engine = create_engine(database_url, echo=False) if database_url else engine
    SQLModel.metadata.create_all(target_engine)
    if target_engine.dialect.name == "sqlite":
        _upgrade_sqlite_schema(target_engine)


create_db_and_tables()

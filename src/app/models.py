from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ConnectionProfile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    base_url: str
    sql_lab_url: str
    auth_mode: str


class JobDefinition(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    execution_mode: str
    sql_template: str | None = None
    params_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    merge_key_columns_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    identity_columns_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_definition_id: int
    status: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None


class RunStepExecution(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int
    step_type: str
    status: str


class DatasetSnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int
    row_count: int
    artifact_path: str
    duckdb_artifact_path: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RuleDefinition(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str
    severity_default: str
    identity_columns_required_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class RuleExecution(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    rule_definition_id: int
    dataset_snapshot_id: int
    status: str


class FindingRuleHit(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_snapshot_id: int
    rule_definition_id: int
    rule_execution_id: int
    identity_key: str
    severity: str
    message: str
    identity_payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class IdentityReviewState(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    identity_key: str
    review_state: str

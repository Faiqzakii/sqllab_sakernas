from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from app.models import DatasetSnapshot, Run
from app.services.findings import list_identity_findings
from app.services.runs import infer_run_type, list_run_steps


@dataclass(frozen=True)
class LatestDatasetSummary:
    row_count: int
    last_successful_update_at: str | None


@dataclass(frozen=True)
class FindingsSummary:
    total: int
    by_severity: dict[str, int]
    by_review_state: dict[str, int]


@dataclass(frozen=True)
class OverviewSummary:
    latest_dataset: LatestDatasetSummary
    anomaly_query: dict[str, str | None]
    findings_summary: FindingsSummary

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _serialize_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _legacy_mtime(*paths: str | None) -> datetime | None:
    timestamps: list[float] = []
    for path in paths:
        if not path:
            continue
        candidate = Path(path)
        if candidate.exists():
            timestamps.append(candidate.stat().st_mtime)
    if not timestamps:
        return None
    return datetime.fromtimestamp(max(timestamps))


def _latest_snapshot(session: Session) -> DatasetSnapshot | None:
    snapshots = session.exec(select(DatasetSnapshot)).all()
    if not snapshots:
        return None
    return max(
        snapshots,
        key=lambda snapshot: (
            snapshot.created_at or datetime.min,
            snapshot.id or 0,
        ),
    )


def _latest_run_by_type(session: Session, run_type: str) -> Run | None:
    runs = session.exec(select(Run)).all()
    matching: list[Run] = []
    for run in runs:
        if run.id is None:
            continue
        if infer_run_type(list_run_steps(session, run.id)) != run_type:
            continue
        matching.append(run)
    if not matching:
        return None
    return max(
        matching,
        key=lambda run: (
            run.completed_at or run.failed_at or run.started_at or run.created_at or datetime.min,
            run.id or 0,
        ),
    )


def build_overview_summary(session: Session) -> OverviewSummary:
    latest_snapshot = _latest_snapshot(session)
    extraction_run = _latest_run_by_type(session, "extraction")
    anomaly_run = _latest_run_by_type(session, "anomaly")
    findings = list_identity_findings(session)

    if latest_snapshot is not None:
        snapshot_run = session.get(Run, latest_snapshot.run_id)
        last_update = (
            (snapshot_run.completed_at if snapshot_run else None)
            or latest_snapshot.created_at
            or _legacy_mtime(latest_snapshot.artifact_path, latest_snapshot.duckdb_artifact_path)
        )
        row_count = latest_snapshot.row_count
    else:
        last_update = extraction_run.completed_at if extraction_run else None
        row_count = 0

    if last_update is None and extraction_run is not None:
        last_update = extraction_run.completed_at or extraction_run.failed_at or extraction_run.started_at or extraction_run.created_at

    findings_by_severity: dict[str, int] = {}
    findings_by_review_state: dict[str, int] = {}
    for item in findings:
        findings_by_severity[item.highest_severity] = findings_by_severity.get(item.highest_severity, 0) + 1
        findings_by_review_state[item.review_state] = findings_by_review_state.get(item.review_state, 0) + 1

    return OverviewSummary(
        latest_dataset=LatestDatasetSummary(
            row_count=row_count,
            last_successful_update_at=_serialize_timestamp(last_update),
        ),
        anomaly_query={
            "last_run_at": _serialize_timestamp(
                anomaly_run.completed_at if anomaly_run else None
            )
        },
        findings_summary=FindingsSummary(
            total=len(findings),
            by_severity=findings_by_severity,
            by_review_state=findings_by_review_state,
        ),
    )

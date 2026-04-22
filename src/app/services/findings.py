from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import ceil
from typing import Any

import pandas as pd
from sqlmodel import Session, select

from app.models import DatasetSnapshot, FindingRuleHit, IdentityReviewState, Run
from app.services.runs import create_and_store_run, get_run_detail, mark_run_completed


SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}
PAYLOAD_FIELD_ALIASES = {
    "nks": ("nks", "NKS"),
    "kode_kab": ("kode_kab", "KODE_KAB", "level_2_code", "LEVEL_2_CODE"),
}


@dataclass
class IdentityFindingAggregate:
    identity_key: str
    highest_severity: str
    rule_ids: list[int]
    identity_payload: dict
    nks: str | None = None
    kode_kab: str | None = None
    review_state: str = "open"


def extract_payload_value(payload: dict[str, Any], *aliases: str) -> str | None:
    if not payload:
        return None

    lowered = {str(key).lower(): value for key, value in payload.items()}
    for alias in aliases:
        value = lowered.get(alias.lower())
        if value in (None, ""):
            continue
        return str(value)
    return None


def payload_dimension(payload: dict[str, Any], field: str) -> str | None:
    aliases = PAYLOAD_FIELD_ALIASES.get(field, (field,))
    return extract_payload_value(payload, *aliases)


def derived_identity_dimension(identity_key: str, field: str) -> str | None:
    if not identity_key:
        return None

    parts = identity_key.split("-")
    prefix = parts[0] if parts else ""
    if not prefix.isdigit():
        return None

    if field == "kode_kab" and len(prefix) >= 4:
        return prefix[2:4]

    if field == "nks":
        if len(parts) >= 2 and parts[1].isdigit():
            return parts[1]

    return None


def aggregate_identity_findings(hits: list[FindingRuleHit]) -> list[IdentityFindingAggregate]:
    grouped: dict[str, list[FindingRuleHit]] = {}
    for hit in hits:
        grouped.setdefault(hit.identity_key, []).append(hit)

    aggregates: list[IdentityFindingAggregate] = []
    for identity_key, identity_hits in grouped.items():
        sorted_hits = sorted(identity_hits, key=lambda item: item.rule_definition_id)
        highest = max(sorted_hits, key=lambda item: SEVERITY_ORDER.get(item.severity, -1))
        aggregates.append(
            IdentityFindingAggregate(
                identity_key=identity_key,
                highest_severity=highest.severity,
                rule_ids=[item.rule_definition_id for item in sorted_hits],
                identity_payload=sorted_hits[0].identity_payload_json,
                nks=payload_dimension(sorted_hits[0].identity_payload_json, "nks") or derived_identity_dimension(identity_key, "nks"),
                kode_kab=payload_dimension(sorted_hits[0].identity_payload_json, "kode_kab") or derived_identity_dimension(identity_key, "kode_kab"),
            )
        )

    return sorted(aggregates, key=lambda item: item.identity_key)


def apply_review_state(aggregate: IdentityFindingAggregate, review_state: str) -> IdentityFindingAggregate:
    return IdentityFindingAggregate(
        identity_key=aggregate.identity_key,
        highest_severity=aggregate.highest_severity,
        rule_ids=list(aggregate.rule_ids),
        identity_payload=dict(aggregate.identity_payload),
        nks=aggregate.nks,
        kode_kab=aggregate.kode_kab,
        review_state=review_state,
    )


def list_identity_findings(session: Session) -> list[IdentityFindingAggregate]:
    hits = session.exec(select(FindingRuleHit)).all()
    aggregates = aggregate_identity_findings(list(hits))
    review_states = {
        row.identity_key: row.review_state
        for row in session.exec(select(IdentityReviewState)).all()
    }
    return [
        IdentityFindingAggregate(
            identity_key=item.identity_key,
            highest_severity=item.highest_severity,
            rule_ids=list(item.rule_ids),
            identity_payload=dict(item.identity_payload),
            nks=item.nks,
            kode_kab=item.kode_kab,
            review_state=review_states.get(item.identity_key, item.review_state),
        )
        for item in aggregates
    ]


def filter_identity_findings(
    session: Session,
    identity_key: str | None = None,
    nks: str | None = None,
    kode_kab: str | None = None,
    severity: str | None = None,
    rule_id: int | None = None,
    review_state: str | None = None,
) -> list[IdentityFindingAggregate]:
    aggregates = list_identity_findings(session)
    results: list[IdentityFindingAggregate] = []
    for item in aggregates:
        if identity_key and identity_key.lower() not in item.identity_key.lower():
            continue
        if nks and nks.lower() not in (item.nks or "").lower():
            continue
        if kode_kab and kode_kab.lower() not in (item.kode_kab or "").lower():
            continue
        if severity and item.highest_severity != severity:
            continue
        if rule_id and rule_id not in item.rule_ids:
            continue
        if review_state and item.review_state != review_state:
            continue
        results.append(item)
    return results


def paginate_identity_findings(
    items: list[IdentityFindingAggregate],
    page: int = 1,
    per_page: int = 20,
) -> dict[str, object]:
    total = len(items)
    total_pages = max(1, ceil(total / per_page)) if total else 1
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": [serialize_identity_finding(item) for item in items[start:end]],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        },
    }


def get_identity_finding(session: Session, identity_key: str) -> IdentityFindingAggregate | None:
    for item in list_identity_findings(session):
        if item.identity_key == identity_key:
            return item
    return None


def set_review_state_for_identity(session: Session, identity_key: str, review_state: str) -> None:
    existing = session.exec(
        select(IdentityReviewState).where(IdentityReviewState.identity_key == identity_key)
    ).first()
    if existing is None:
        session.add(IdentityReviewState(identity_key=identity_key, review_state=review_state))
    else:
        existing.review_state = review_state
        session.add(existing)
    session.commit()


def serialize_identity_finding(item: IdentityFindingAggregate) -> dict[str, object]:
    return {
        "identity_key": item.identity_key,
        "nks": item.nks,
        "kode_kab": item.kode_kab,
        "highest_severity": item.highest_severity,
        "rule_ids": list(item.rule_ids),
        "identity_payload": dict(item.identity_payload),
        "review_state": item.review_state,
    }


def findings_export_frame(items: list[IdentityFindingAggregate]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "identity_key": item.identity_key,
                "nks": item.nks or "",
                "kode_kab": item.kode_kab or "",
                "highest_severity": item.highest_severity,
                "review_state": item.review_state,
                "rule_ids": ", ".join(str(rule_id) for rule_id in item.rule_ids),
                "identity_payload_json": json.dumps(item.identity_payload, ensure_ascii=False),
            }
            for item in items
        ],
        columns=[
            "identity_key",
            "nks",
            "kode_kab",
            "highest_severity",
            "review_state",
            "rule_ids",
            "identity_payload_json",
        ],
    )


def trigger_anomaly_run(session: Session, dataset_snapshot_id: int) -> dict[str, object]:
    snapshot = session.get(DatasetSnapshot, dataset_snapshot_id)
    if snapshot is None:
        raise ValueError(f"Dataset snapshot not found: {dataset_snapshot_id}")

    parent_run = session.get(Run, snapshot.run_id)
    job_definition_id = parent_run.job_definition_id if parent_run is not None else 0
    run = create_and_store_run(session, job_definition_id=job_definition_id, step_types=["anomaly_scan", "finding_aggregate"])
    run.started_at = datetime.now(UTC)
    session.add(run)
    session.commit()
    session.refresh(run)
    completed = mark_run_completed(session, run.id)
    return get_run_detail(session, completed.id)

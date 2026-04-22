from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import require_admin_session
from app.db import engine
from app.models import FindingRuleHit
from app.services.findings import (
    aggregate_identity_findings,
    findings_export_frame,
    filter_identity_findings,
    get_identity_finding,
    list_identity_findings,
    paginate_identity_findings,
    set_review_state_for_identity,
    serialize_identity_finding,
)


router = APIRouter()


class ReviewStateRequest(BaseModel):
    review_state: str


def _sample_hits() -> list[FindingRuleHit]:
    with Session(engine) as session:
        existing = session.exec(select(FindingRuleHit)).all()
        if existing:
            return list(existing)

        seeded = [
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=1,
                rule_execution_id=1,
                identity_key="id-001",
                severity="warn",
                message="missing field",
                identity_payload_json={"identity_key": "id-001", "household_number": "12", "NKS": "20250434", "KODE_KAB": "01"},
            ),
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=2,
                rule_execution_id=2,
                identity_key="id-001",
                severity="critical",
                message="duplicate record",
                identity_payload_json={"identity_key": "id-001", "household_number": "12", "NKS": "20250434", "KODE_KAB": "01"},
            ),
        ]
        for hit in seeded:
            session.add(hit)
        session.commit()
        return seeded


@router.get("/identity-findings")
def list_identity_findings_route() -> list[dict]:
    _sample_hits()
    with Session(engine) as session:
        return [serialize_identity_finding(aggregate) for aggregate in list_identity_findings(session)]


@router.post("/identity-findings/{identity_key}/status")
def update_identity_review_state(identity_key: str, request: ReviewStateRequest) -> dict:
    _sample_hits()
    with Session(engine) as session:
        set_review_state_for_identity(session, identity_key, request.review_state)
        aggregate = next(item for item in list_identity_findings(session) if item.identity_key == identity_key)
        return serialize_identity_finding(aggregate)


@router.get("/api/v1/findings")
def list_findings(
    identity_key: str | None = None,
    nks: str | None = None,
    kode_kab: str | None = None,
    severity: str | None = None,
    rule_id: int | None = None,
    review_state: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, object]:
    with Session(engine) as session:
        findings = filter_identity_findings(
            session,
            identity_key=identity_key,
            nks=nks,
            kode_kab=kode_kab,
            severity=severity,
            rule_id=rule_id,
            review_state=review_state,
        )
        return paginate_identity_findings(findings, page=page, per_page=per_page)


@router.get("/api/v1/findings/export.xlsx")
def export_findings_excel(
    identity_key: str | None = None,
    nks: str | None = None,
    kode_kab: str | None = None,
    severity: str | None = None,
    rule_id: int | None = None,
    review_state: str | None = None,
) -> Response:
    with Session(engine) as session:
        findings = filter_identity_findings(
            session,
            identity_key=identity_key,
            nks=nks,
            kode_kab=kode_kab,
            severity=severity,
            rule_id=rule_id,
            review_state=review_state,
        )

    dataframe = findings_export_frame(findings)
    buffer = BytesIO()
    dataframe.to_excel(buffer, index=False, engine="openpyxl")
    filename = f"findings-export-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/v1/findings/{identity_key}")
def get_finding_detail(identity_key: str) -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        finding = get_identity_finding(session, identity_key)
        if finding is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "finding_not_found", "message": "Finding not found"}})
        return {"data": serialize_identity_finding(finding)}


@router.patch("/api/v1/findings/{identity_key}/review-state", dependencies=[Depends(require_admin_session)])
def update_review_state(identity_key: str, request: ReviewStateRequest) -> dict[str, dict[str, object]]:
    with Session(engine) as session:
        if get_identity_finding(session, identity_key) is None:
            raise HTTPException(status_code=404, detail={"error": {"code": "finding_not_found", "message": "Finding not found"}})
        set_review_state_for_identity(session, identity_key, request.review_state)
        updated = get_identity_finding(session, identity_key)
        return {"data": {"identity_key": identity_key, "review_state": updated.review_state if updated else request.review_state}}

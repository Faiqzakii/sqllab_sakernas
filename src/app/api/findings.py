from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import engine
from app.models import FindingRuleHit
from app.services.findings import aggregate_identity_findings, list_identity_findings, set_review_state_for_identity


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
                identity_payload_json={"identity_key": "id-001", "household_number": "12"},
            ),
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=2,
                rule_execution_id=2,
                identity_key="id-001",
                severity="critical",
                message="duplicate record",
                identity_payload_json={"identity_key": "id-001", "household_number": "12"},
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
        return [aggregate.__dict__ for aggregate in list_identity_findings(session)]


@router.post("/identity-findings/{identity_key}/status")
def update_identity_review_state(identity_key: str, request: ReviewStateRequest) -> dict:
    _sample_hits()
    with Session(engine) as session:
        set_review_state_for_identity(session, identity_key, request.review_state)
        aggregate = next(item for item in list_identity_findings(session) if item.identity_key == identity_key)
        return aggregate.__dict__

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models import FindingRuleHit, IdentityReviewState


SEVERITY_ORDER = {"info": 0, "warn": 1, "critical": 2}


@dataclass
class IdentityFindingAggregate:
    identity_key: str
    highest_severity: str
    rule_ids: list[int]
    identity_payload: dict
    review_state: str = "open"


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
            )
        )

    return sorted(aggregates, key=lambda item: item.identity_key)


def apply_review_state(aggregate: IdentityFindingAggregate, review_state: str) -> IdentityFindingAggregate:
    return IdentityFindingAggregate(
        identity_key=aggregate.identity_key,
        highest_severity=aggregate.highest_severity,
        rule_ids=list(aggregate.rule_ids),
        identity_payload=dict(aggregate.identity_payload),
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
            review_state=review_states.get(item.identity_key, item.review_state),
        )
        for item in aggregates
    ]


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

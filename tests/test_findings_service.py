from app.models import FindingRuleHit
from app.services.findings import aggregate_identity_findings, apply_review_state


def test_apply_review_state_updates_aggregate_review_state() -> None:
    hits = [
        FindingRuleHit(
            dataset_snapshot_id=1,
            rule_definition_id=1,
            rule_execution_id=1,
            identity_key="id-001",
            severity="warn",
            message="missing field",
            identity_payload_json={"identity_key": "id-001", "household_number": "12"},
        )
    ]

    aggregate = aggregate_identity_findings(hits)[0]
    updated = apply_review_state(aggregate, "acknowledged")

    assert updated.review_state == "acknowledged"

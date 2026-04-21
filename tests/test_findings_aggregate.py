from app.models import FindingRuleHit
from app.services.findings import aggregate_identity_findings


def test_multiple_rule_hits_aggregate_into_one_identity_record() -> None:
    hits = [
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
            message="duplicate member",
            identity_payload_json={"identity_key": "id-001", "household_number": "12"},
        ),
    ]

    aggregates = aggregate_identity_findings(hits)

    assert len(aggregates) == 1
    assert aggregates[0].identity_key == "id-001"
    assert aggregates[0].highest_severity == "critical"
    assert aggregates[0].rule_ids == [1, 2]

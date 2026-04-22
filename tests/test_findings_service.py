from sqlmodel import Session

import app.db as db_module
from app.models import FindingRuleHit
from app.services.findings import aggregate_identity_findings, apply_review_state, filter_identity_findings


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


def test_filter_identity_findings_supports_nks_and_kode_kab_aliases() -> None:
    with Session(db_module.engine) as session:
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=1,
                rule_execution_id=1,
                identity_key="id-001",
                severity="warn",
                message="missing field",
                identity_payload_json={"identity_key": "id-001", "NKS": "20250434", "KODE_KAB": "71"},
            )
        )
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=2,
                rule_execution_id=2,
                identity_key="id-002",
                severity="critical",
                message="duplicate field",
                identity_payload_json={"identity_key": "id-002", "NKS": "20250000", "KODE_KAB": "11"},
            )
        )
        session.commit()

        filtered = filter_identity_findings(session, nks="20250434", kode_kab="71")

    assert [item.identity_key for item in filtered] == ["id-001"]
    assert filtered[0].nks == "20250434"
    assert filtered[0].kode_kab == "71"

from fastapi.testclient import TestClient

from app.main import create_app


def test_create_run_endpoint_returns_pending_run() -> None:
    client = TestClient(create_app())

    response = client.post("/runs", json={"job_definition_id": 1, "step_types": ["superset_sql", "local_python"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert [step["step_type"] for step in payload["steps"]] == ["superset_sql", "local_python"]


def test_identity_findings_endpoint_groups_hits() -> None:
    client = TestClient(create_app())

    response = client.get("/identity-findings")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["identity_key"] == "id-001"
    assert payload[0]["highest_severity"] == "critical"


def test_identity_findings_review_state_update_endpoint() -> None:
    client = TestClient(create_app())

    response = client.post("/identity-findings/id-001/status", json={"review_state": "acknowledged"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_state"] == "acknowledged"

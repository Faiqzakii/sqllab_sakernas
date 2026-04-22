from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

import app.db as db_module
from app.main import create_app
from app.models import FindingRuleHit


def login_admin(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def seed_finding(identity_key: str) -> None:
    with Session(db_module.engine) as session:
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=99,
                rule_execution_id=99,
                identity_key=identity_key,
                severity="critical",
                message="synthetic critical issue",
                identity_payload_json={"identity_key": identity_key, "household_number": "12"},
            )
        )
        session.commit()


def test_viewer_cannot_create_run() -> None:
    client = TestClient(create_app())

    response = client.post("/runs", json={"job_definition_id": 1, "step_types": ["superset_sql", "local_python"]})

    assert response.status_code == 403


def test_create_run_endpoint_returns_pending_run_for_admin() -> None:
    client = TestClient(create_app())
    login_admin(client)

    response = client.post("/runs", json={"job_definition_id": 1, "step_types": ["superset_sql", "local_python"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["run_type"] == "extraction"
    assert [step["step_type"] for step in payload["steps"]] == ["superset_sql", "local_python"]


def test_list_runs_returns_recent_execution_history() -> None:
    client = TestClient(create_app())
    login_admin(client)

    created = client.post("/runs", json={"job_definition_id": 1, "step_types": ["superset_sql", "snapshot_merge"]})
    history = client.get("/api/v1/run-control/runs")

    assert created.status_code == 200
    assert history.status_code == 200
    payload = history.json()
    assert payload["meta"]["total"] >= 1
    assert any(item["run_id"] == created.json()["run_id"] for item in payload["data"])


def test_get_run_detail_returns_steps_and_status() -> None:
    client = TestClient(create_app())
    login_admin(client)

    created = client.post("/runs", json={"job_definition_id": 1, "step_types": ["superset_sql", "snapshot_merge"]})
    run_id = created.json()["run_id"]
    detail = client.get(f"/api/v1/run-control/runs/{run_id}")

    assert detail.status_code == 200
    payload = detail.json()["data"]
    assert payload["run_id"] == run_id
    assert payload["status"] == "pending"
    assert len(payload["steps"]) == 2


def test_extraction_run_control_returns_structured_error_when_source_path_missing() -> None:
    client = TestClient(create_app())
    login_admin(client)

    created = client.post(
        "/job-definitions",
        json={
            "name": f"missing-source-{uuid4().hex[:8]}",
            "execution_mode": "superset_sql",
            "sql_template": "select 1",
            "params_schema_json": {"source_data_path": "data/does-not-exist.json"},
            "merge_key_columns_json": ["identity_key"],
            "identity_columns_json": ["identity_key"],
        },
    )
    response = client.post(
        "/api/v1/run-control/extraction",
        json={"job_definition_id": created.json()["id"], "debug": False},
    )

    assert response.status_code == 500
    assert "does-not-exist.json" in response.json()["detail"]["error"]["message"]


def test_run_control_extraction_uses_superset_executor_factory(monkeypatch) -> None:
    client = TestClient(create_app())
    login_admin(client)

    class FakeExecutor:
        def __init__(self) -> None:
            self.sql_calls: list[str] = []

        def run_query(self, sql: str):
            self.sql_calls.append(sql)

            import pandas as pd

            class Result:
                source = "ui"
                metadata = {"row_count": 1}

                def __init__(self) -> None:
                    self.dataframe = pd.DataFrame(
                        [
                            {
                                "KODE_PROV": "65",
                                "KODE_KAB": "01",
                                "KODE_KEC": "030",
                                "KODE_DESA": "004",
                                "SLS": "0028",
                                "SUBSLS": "00",
                                "NKS": "20250434",
                                "DSRT": 10,
                                "NO_ART": "3",
                                "identity_key": "6501030004002800-20250434-10-3",
                            }
                        ]
                    )

            return Result()

        def close(self) -> None:
            return None

    fake_executor = FakeExecutor()
    monkeypatch.setattr("app.api.runs.build_superset_executor", lambda job: fake_executor)

    created = client.post(
        "/job-definitions",
        json={
            "name": f"sequential-run-{uuid4().hex[:8]}",
            "execution_mode": "superset_sql",
            "sql_template": "SELECT ... WHERE art.level_2_code='{{ level_2_code }}'",
            "params_schema_json": {
                "base_url": "https://superset.local",
                "sql_lab_url": "https://superset.local/sqllab/",
                "batching_strategy": {
                    "type": "explicit_list",
                    "param": "level_2_code",
                    "values": ["01", "02", "03", "04", "71"],
                },
            },
            "merge_key_columns_json": ["identity_key"],
            "identity_columns_json": ["identity_key"],
        },
    )
    response = client.post(
        "/api/v1/run-control/extraction",
        json={"job_definition_id": created.json()["id"], "debug": False},
    )

    assert response.status_code == 202
    assert len(fake_executor.sql_calls) == 5


def test_identity_findings_endpoint_groups_hits() -> None:
    client = TestClient(create_app())
    unique_identity = f"id-{uuid4().hex[:8]}"
    seed_finding(unique_identity)

    response = client.get(f"/api/v1/findings?identity_key={unique_identity}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"][0]["identity_key"] == unique_identity
    assert payload["data"][0]["highest_severity"] == "critical"


def test_identity_findings_detail_and_review_state_update_endpoints() -> None:
    client = TestClient(create_app())
    unique_identity = f"id-{uuid4().hex[:8]}"
    seed_finding(unique_identity)

    detail = client.get(f"/api/v1/findings/{unique_identity}")
    forbidden = client.patch(f"/api/v1/findings/{unique_identity}/review-state", json={"review_state": "reviewed"})

    assert detail.status_code == 200
    assert detail.json()["data"]["identity_key"] == unique_identity
    assert forbidden.status_code == 403

    login_admin(client)
    updated = client.patch(f"/api/v1/findings/{unique_identity}/review-state", json={"review_state": "reviewed"})

    assert updated.status_code == 200
    assert updated.json()["data"]["review_state"] == "reviewed"

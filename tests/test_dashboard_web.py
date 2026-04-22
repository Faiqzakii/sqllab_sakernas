from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

import app.db as db_module
from app.main import create_app
from app.models import FindingRuleHit, JobDefinition


def login_admin(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def seed_job_definition(name: str) -> None:
    with Session(db_module.engine) as session:
        session.add(
            JobDefinition(
                name=name,
                execution_mode="superset_sql",
                sql_template="select * from some_table",
                params_schema_json={
                    "base_url": "https://superset.local",
                    "sql_lab_url": "https://superset.local/sqllab/",
                    "source_data_path": "data/source.json",
                    "batching_strategy": {"type": "explicit_list", "param": "level_2_code", "values": ["01", "02"]},
                },
                merge_key_columns_json=["identity_key"],
                identity_columns_json=["identity_key"],
            )
        )
        session.commit()


def seed_finding(identity_key: str, nks: str = "20250434", kode_kab: str = "01") -> None:
    with Session(db_module.engine) as session:
        session.add(
            FindingRuleHit(
                dataset_snapshot_id=1,
                rule_definition_id=1,
                rule_execution_id=1,
                identity_key=identity_key,
                severity="critical",
                message="synthetic critical issue",
                identity_payload_json={
                    "identity_key": identity_key,
                    "household_number": "12",
                    "NKS": nks,
                    "KODE_KAB": kode_kab,
                },
            )
        )
        session.commit()


def test_overview_page_shows_only_approved_summary_sections() -> None:
    client = TestClient(create_app())

    response = client.get("/ui/overview")

    assert response.status_code == 200
    assert "Skip ke konten utama" in response.text
    assert "Latest dataset size" in response.text
    assert "Last successful refresh" in response.text
    assert "Last anomaly sweep" in response.text
    assert "Current findings mix" in response.text
    assert "Run extraction job" not in response.text
    assert "SQL template" not in response.text
    assert "Admin login" not in response.text


def test_findings_page_lists_identity_findings_for_viewer() -> None:
    client = TestClient(create_app())
    unique_identity = f"id-{uuid4().hex[:8]}"
    seed_finding(unique_identity)

    response = client.get(f"/ui/findings?identity_key={unique_identity}&selected={unique_identity}")

    assert response.status_code == 200
    assert unique_identity in response.text
    assert "Selected finding detail" in response.text


def test_findings_page_supports_nks_and_kode_kab_filters_with_export() -> None:
    client = TestClient(create_app())
    matching_identity = f"id-{uuid4().hex[:8]}"
    other_identity = f"id-{uuid4().hex[:8]}"
    seed_finding(matching_identity, nks="20250434", kode_kab="71")
    seed_finding(other_identity, nks="99999999", kode_kab="11")

    response = client.get(f"/ui/findings?nks=20250434&kode_kab=71&selected={matching_identity}")

    assert response.status_code == 200
    assert matching_identity in response.text
    assert other_identity not in response.text
    assert "Export Excel" in response.text


def test_login_promotes_viewer_to_admin_session() -> None:
    client = TestClient(create_app())

    login_page = client.get("/ui/login")
    login_admin(client)
    overview = client.get("/ui/overview")

    assert login_page.status_code == 200
    assert "Admin login" in login_page.text
    assert "Configuration" in overview.text
    assert "Run Control" in overview.text


def test_viewer_cannot_open_configuration_page() -> None:
    client = TestClient(create_app())

    response = client.get("/ui/configuration")

    assert response.status_code == 403
    assert "Admin access required" in response.text


def test_admin_configuration_page_shows_jobdefinition_fields() -> None:
    client = TestClient(create_app())
    unique_name = f"config-page-{uuid4().hex[:8]}"
    seed_job_definition(unique_name)
    login_admin(client)

    response = client.get("/ui/configuration")

    assert response.status_code == 200
    assert unique_name in response.text
    assert "SQL template" in response.text
    assert "Batching values" in response.text


def test_admin_run_control_page_shows_run_actions_and_monitor() -> None:
    client = TestClient(create_app())
    unique_name = f"run-control-{uuid4().hex[:8]}"
    seed_job_definition(unique_name)
    login_admin(client)

    response = client.get("/ui/run-control")

    assert response.status_code == 200
    assert "Run extraction job" in response.text
    assert "Run anomaly/findings" in response.text
    assert "Recent runs" in response.text
    assert "Run detail" in response.text

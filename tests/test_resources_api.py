from fastapi.testclient import TestClient

from app.main import create_app
from app.sample_queries import SIMULATED_COMPLETE_DATA_BATCHING, SIMULATED_COMPLETE_DATA_SQL_TEMPLATE


def test_job_definitions_create_and_list() -> None:
    client = TestClient(create_app())

    create_response = client.post(
        "/job-definitions",
        json={
            "name": "household-sync",
            "execution_mode": "superset_sql",
            "sql_template": SIMULATED_COMPLETE_DATA_SQL_TEMPLATE,
            "params_schema_json": {"batching_strategy": SIMULATED_COMPLETE_DATA_BATCHING},
            "merge_key_columns_json": ["identity_key"],
            "identity_columns_json": ["identity_key", "household_number"],
        },
    )
    list_response = client.get("/job-definitions")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(item["name"] == "household-sync" for item in list_response.json())
    assert any("art.level_2_code='{{ level_2_code }}'" in item["sql_template"] for item in list_response.json())
    assert any(
        item["params_schema_json"].get("batching_strategy", {}).get("values") == ["01", "02", "03", "04", "71"]
        for item in list_response.json()
    )


def test_rule_definitions_create_and_list() -> None:
    client = TestClient(create_app())

    create_response = client.post(
        "/rule-definitions",
        json={
            "name": "missing-household-number",
            "kind": "python",
            "severity_default": "warn",
            "identity_columns_required_json": ["identity_key", "household_number"],
        },
    )
    list_response = client.get("/rule-definitions")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(item["name"] == "missing-household-number" for item in list_response.json())


def test_snapshots_list_persisted_records() -> None:
    client = TestClient(create_app())

    create_response = client.post(
        "/snapshots",
        json={
            "run_id": 1,
            "row_count": 25,
            "artifact_path": "artifacts/snapshots/1/dataset.parquet",
        },
    )
    list_response = client.get("/snapshots")

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert any(item["row_count"] == 25 for item in list_response.json())


def test_execute_job_endpoint_uses_injected_superset_executor_factory(monkeypatch) -> None:
    client = TestClient(create_app())

    class FakeExecutor:
        def __init__(self) -> None:
            self.sql_calls: list[str] = []

        def run_query(self, sql: str):
            self.sql_calls.append(sql)

            import pandas as pd

            class Result:
                source = "backend"
                metadata = {"status_code": 200}

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
                            }
                        ]
                    )

            return Result()

    fake_executor = FakeExecutor()
    monkeypatch.setattr("app.api.resources.build_superset_executor", lambda job: fake_executor)

    create_response = client.post(
        "/job-definitions",
        json={
            "name": "household-sync-live",
            "execution_mode": "superset_sql",
            "sql_template": "SELECT ... WHERE art.level_2_code='{{ level_2_code }}'",
            "params_schema_json": {
                "batching_strategy": {
                    "type": "explicit_list",
                    "param": "level_2_code",
                    "values": ["01", "02", "03", "04", "71"],
                }
            },
            "merge_key_columns_json": ["identity_key"],
            "identity_columns_json": ["identity_key", "household_number"],
        },
    )

    job_id = create_response.json()["id"]
    execute_response = client.post(f"/job-definitions/{job_id}/execute")

    assert execute_response.status_code == 200
    assert len(fake_executor.sql_calls) == 5

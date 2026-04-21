from app.engine.superset_auth import AuthBootstrapResult
from app.engine.superset_client import QueryResult, SupersetClient


def test_superset_engine_contracts_are_importable() -> None:
    result = QueryResult(dataframe=None, metadata={"status_code": 200}, source="backend")

    assert AuthBootstrapResult is not None
    assert result.source == "backend"
    assert callable(SupersetClient.run_query)

from types import SimpleNamespace

import pandas as pd

import app.engine.superset_executor as executor_module
from app.engine.superset_client import QueryResult
from app.engine.superset_executor import SequentialSupersetExecutor


def test_sequential_superset_executor_uses_fresh_ui_runner_per_query(monkeypatch) -> None:
    init_calls: list[dict[str, object]] = []
    query_calls: list[str] = []

    class FakeUiRunner:
        def __init__(self, **kwargs) -> None:
            init_calls.append(kwargs)

        def run_query(self, sql: str) -> QueryResult:
            query_calls.append(sql)
            return QueryResult(
                dataframe=pd.DataFrame([{"KODE_KAB": "01"}]),
                metadata={"row_count": 1},
                source="ui",
            )

    closed = {"value": False}

    class FakeAuthResult(SimpleNamespace):
        def close(self) -> None:
            closed["value"] = True

    monkeypatch.setattr(executor_module, "SupersetUiRunner", FakeUiRunner)

    auth_result = FakeAuthResult(
        cookies=[{"name": "session", "value": "abc"}],
        browser="browser",
        context="context",
    )
    executor = SequentialSupersetExecutor(
        sql_lab_url="https://example.test/superset/sqllab/",
        auth_result=auth_result,
    )

    first = executor.run_query("SELECT 1")
    second = executor.run_query("SELECT 2")
    executor.close()

    assert first.source == "ui"
    assert second.source == "ui"
    assert query_calls == ["SELECT 1", "SELECT 2"]
    assert len(init_calls) == 2
    assert all(call["page"] is None for call in init_calls)
    assert all(call["context"] == "context" for call in init_calls)
    assert closed["value"] is True

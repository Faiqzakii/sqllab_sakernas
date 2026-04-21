from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd
import requests


@dataclass(frozen=True)
class QueryResult:
    dataframe: pd.DataFrame | None
    metadata: dict[str, Any]
    source: str


class UiRunner(Protocol):
    def run_query(self, sql: str) -> QueryResult: ...


def normalize_sql_json_to_dataframe(payload: dict[str, Any]) -> pd.DataFrame:
    rows = payload.get("data")
    if isinstance(rows, list):
        return pd.DataFrame(rows)

    rows = payload.get("result")
    if isinstance(rows, list):
        return pd.DataFrame(rows)

    return pd.DataFrame()


def has_sql_json_rows(payload: dict[str, Any]) -> bool:
    data_rows = payload.get("data")
    if isinstance(data_rows, list):
        return True

    result_rows = payload.get("result")
    if isinstance(result_rows, list):
        return True

    return False


class SupersetClient:
    def __init__(self, session: requests.Session, base_url: str, ui_runner: UiRunner | None = None) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.ui_runner = ui_runner

    def run_query(self, sql: str) -> QueryResult:
        backend_result = self._run_query_backend(sql)
        if backend_result is not None:
            return backend_result

        if self.ui_runner is None:
            raise RuntimeError("Backend query execution failed and no UI fallback is configured")

        return self.ui_runner.run_query(sql)

    def _run_query_backend(self, sql: str) -> QueryResult | None:
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/sqllab/execute/",
                json={"sql": sql},
                timeout=60,
            )
        except requests.RequestException:
            return None

        if not response.ok:
            return None

        try:
            payload = response.json()
        except ValueError:
            return None

        if not isinstance(payload, dict):
            return None

        if not has_sql_json_rows(payload):
            return None

        dataframe = normalize_sql_json_to_dataframe(payload)
        if dataframe.empty:
            return None

        return QueryResult(
            dataframe=dataframe,
            metadata={"status_code": response.status_code},
            source="backend",
        )

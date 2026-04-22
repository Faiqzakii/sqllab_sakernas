from __future__ import annotations

from typing import Any

from app.engine.superset_auth import AuthBootstrapResult, SupersetAuthBootstrap
from app.engine.superset_ui_runner import SupersetUiRunner
from app.models import JobDefinition


class SequentialSupersetExecutor:
    def __init__(
        self,
        *,
        sql_lab_url: str,
        auth_result: AuthBootstrapResult,
        debug_callback: Any | None = None,
    ) -> None:
        self.sql_lab_url = sql_lab_url
        self.auth_result = auth_result
        self.debug_callback = debug_callback

    def run_query(self, sql: str):
        # Use a fresh SQL Lab page per batch to reduce shared state bleed.
        ui_runner = SupersetUiRunner(
            sql_lab_url=self.sql_lab_url,
            auth_cookies=self.auth_result.cookies,
            browser=self.auth_result.browser,
            context=self.auth_result.context,
            page=None,
            debug_callback=self.debug_callback,
        )
        return ui_runner.run_query(sql)

    def close(self) -> None:
        self.auth_result.close()


def build_superset_executor(job: JobDefinition):
    if job.execution_mode != "superset_sql":
        return None

    params = job.params_schema_json if isinstance(job.params_schema_json, dict) else {}
    base_url = params.get("base_url")
    sql_lab_url = params.get("sql_lab_url")
    if not isinstance(base_url, str) or not isinstance(sql_lab_url, str):
        return None

    auth = SupersetAuthBootstrap(base_url=base_url, sql_lab_url=sql_lab_url)
    auth_result = auth.login_and_capture()
    return SequentialSupersetExecutor(
        sql_lab_url=sql_lab_url,
        auth_result=auth_result,
    )

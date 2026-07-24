from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import app.engine.superset_query_runner as query_runner_module
from app.engine.superset_client import QueryResult
from app.engine.superset_query_runner import (
    SupersetQueryRunner,
    is_bot_block_error,
    is_recoverable_session_error,
)


@pytest.mark.parametrize(
    "message",
    [
        "SQL Lab returned HTML instead of JSON (possible logout/session expiry; url=...)",
        "Unexpected '<'",
        'Unexpected "<"',
        "401 unauthorized",
        "csrf token missing",
        "session expired",
        "Database error\n\nUnexpected '<'",
    ],
)
def test_is_recoverable_session_error_matches_html_and_auth(message: str) -> None:
    assert is_recoverable_session_error(RuntimeError(message))


def test_is_bot_block_error_matches_waf_label() -> None:
    assert is_bot_block_error(
        RuntimeError("WAF bot block (BOT-1): SQL Lab execute returned HTML (not session logout)")
    )
    assert is_recoverable_session_error(
        RuntimeError("WAF bot block (BOT-1): SQL Lab execute returned HTML")
    )


def test_is_recoverable_session_error_ignores_sql_bugs() -> None:
    assert not is_recoverable_session_error(RuntimeError("column assignment_id not found"))
    assert not is_recoverable_session_error(RuntimeError("Editor SQL did not update"))


def test_query_runner_force_refreshes_then_retries_on_html_error(monkeypatch) -> None:
    login_calls = {"n": 0}
    recover_calls = {"n": 0}
    run_calls: list[str] = []

    class FakeAuthResult(SimpleNamespace):
        def close(self) -> None:
            return None

    class FakeAuth:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def login_and_capture(self) -> FakeAuthResult:
            login_calls["n"] += 1
            return FakeAuthResult(
                cookies=[{"name": "session", "value": f"tok-{login_calls['n']}"}],
                browser="browser",
                context="context",
                page=SimpleNamespace(url="https://example.test/superset/sqllab/"),
            )

        def build_requests_session(self, cookies):
            return SimpleNamespace(cookies=cookies)

    class FakeUiRunner:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def run_query(self, sql: str) -> QueryResult:
            run_calls.append(sql)
            if len(run_calls) == 1:
                raise RuntimeError(
                    "SQL Lab returned HTML instead of JSON "
                    "(possible logout/session expiry; url=/api/v1/sqllab/execute/)"
                )
            return QueryResult(
                dataframe=pd.DataFrame([{"assignment_id": "ok"}]),
                metadata={"row_count": 1},
                source="ui",
            )

    monkeypatch.setattr(query_runner_module, "SupersetAuthBootstrap", FakeAuth)
    monkeypatch.setattr(query_runner_module, "SupersetUiRunner", FakeUiRunner)

    runner = SupersetQueryRunner(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        mode="ui",
        max_session_recoveries=1,
    )

    def fake_recover() -> None:
        recover_calls["n"] += 1

    monkeypatch.setattr(runner, "recover_session", fake_recover)

    frame = runner("SELECT 1")
    assert frame.to_dict(orient="records") == [{"assignment_id": "ok"}]
    assert recover_calls["n"] == 1
    assert run_calls == ["SELECT 1", "SELECT 1"]
    assert login_calls["n"] == 1


def test_query_runner_relLogin_when_page_on_login(monkeypatch) -> None:
    login_calls = {"n": 0}
    goto_calls: list[str] = []

    class FakePage:
        def __init__(self) -> None:
            self.url = "https://example.test/login/"

        def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
            goto_calls.append(url)
            self.url = url

        def evaluate(self, script: str, arg=None):
            return "/login" in self.url

        def wait_for_function(self, script: str, arg=None, timeout=None) -> None:
            return None

    class FakeAuthResult(SimpleNamespace):
        def close(self) -> None:
            return None

    class FakeAuth:
        def __init__(self, **kwargs) -> None:
            pass

        def login_and_capture(self) -> FakeAuthResult:
            login_calls["n"] += 1
            page = FakePage()
            if login_calls["n"] >= 2:
                page.url = "https://example.test/superset/sqllab/"
            return FakeAuthResult(
                cookies=[{"name": "session", "value": f"tok-{login_calls['n']}"}],
                browser="browser",
                context="context",
                page=page,
            )

        def build_requests_session(self, cookies):
            return SimpleNamespace(cookies=cookies)

    monkeypatch.setattr(query_runner_module, "SupersetAuthBootstrap", FakeAuth)
    monkeypatch.setattr(query_runner_module, "wait_for_sql_lab_editor_ready", lambda page: None)
    monkeypatch.setattr(query_runner_module, "navigate_sql_lab", lambda page, url, timeout_ms=0: None)

    runner = SupersetQueryRunner(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        mode="ui",
    )
    assert login_calls["n"] == 1
    assert runner._page_looks_logged_out() is True
    runner.recover_session()
    assert login_calls["n"] == 2
    assert runner.auth_result.page.url.endswith("/superset/sqllab/")


def test_query_runner_does_not_recover_non_session_errors(monkeypatch) -> None:
    class FakeAuthResult(SimpleNamespace):
        def close(self) -> None:
            return None

    class FakeAuth:
        def __init__(self, **kwargs) -> None:
            pass

        def login_and_capture(self) -> FakeAuthResult:
            return FakeAuthResult(
                cookies=[],
                browser=None,
                context=None,
                page=SimpleNamespace(url="https://example.test/superset/sqllab/"),
            )

        def build_requests_session(self, cookies):
            return SimpleNamespace(cookies=cookies)

    class FakeUiRunner:
        def __init__(self, **kwargs) -> None:
            pass

        def run_query(self, sql: str) -> QueryResult:
            raise RuntimeError("Editor SQL did not update to the requested batch query")

    monkeypatch.setattr(query_runner_module, "SupersetAuthBootstrap", FakeAuth)
    monkeypatch.setattr(query_runner_module, "SupersetUiRunner", FakeUiRunner)

    runner = SupersetQueryRunner(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        mode="ui",
    )
    with pytest.raises(RuntimeError, match="Editor SQL did not update"):
        runner("SELECT 1")


def test_query_runner_bot_block_uses_backoff_then_retries(monkeypatch) -> None:
    bot_recover_calls: list[int] = []
    run_calls: list[str] = []

    class FakeAuthResult(SimpleNamespace):
        def close(self) -> None:
            return None

    class FakeAuth:
        def __init__(self, **kwargs) -> None:
            pass

        def login_and_capture(self) -> FakeAuthResult:
            return FakeAuthResult(
                cookies=[{"name": "session", "value": "tok"}],
                browser="browser",
                context="context",
                page=SimpleNamespace(url="https://example.test/superset/sqllab/"),
            )

        def build_requests_session(self, cookies):
            return SimpleNamespace(cookies=cookies)

    class FakeUiRunner:
        def __init__(self, **kwargs) -> None:
            pass

        def run_query(self, sql: str) -> QueryResult:
            run_calls.append(sql)
            if len(run_calls) == 1:
                raise RuntimeError(
                    "WAF bot block (BOT-12236185493749556630): SQL Lab execute returned HTML "
                    "(not session logout; url=/api/v1/sqllab/execute/)"
                )
            return QueryResult(
                dataframe=pd.DataFrame([{"assignment_id": "ok"}]),
                metadata={"row_count": 1},
                source="ui",
            )

    monkeypatch.setattr(query_runner_module, "SupersetAuthBootstrap", FakeAuth)
    monkeypatch.setattr(query_runner_module, "SupersetUiRunner", FakeUiRunner)

    runner = SupersetQueryRunner(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        mode="ui",
        max_bot_recoveries=2,
        bot_recovery_backoff_seconds=(0.0, 0.0),
    )

    def fake_bot_recover(attempt: int) -> None:
        bot_recover_calls.append(attempt)

    monkeypatch.setattr(runner, "recover_bot_block", fake_bot_recover)

    frame = runner("SELECT 1")
    assert frame.to_dict(orient="records") == [{"assignment_id": "ok"}]
    assert bot_recover_calls == [1]
    assert run_calls == ["SELECT 1", "SELECT 1"]


def test_query_runner_bot_block_escalates_to_relogin(monkeypatch) -> None:
    refresh_calls = {"n": 0}
    relogin_calls = {"n": 0}
    run_calls: list[str] = []

    class FakeAuthResult(SimpleNamespace):
        def close(self) -> None:
            return None

    class FakeAuth:
        def __init__(self, **kwargs) -> None:
            pass

        def login_and_capture(self) -> FakeAuthResult:
            return FakeAuthResult(
                cookies=[{"name": "session", "value": "tok"}],
                browser="browser",
                context="context",
                page=SimpleNamespace(url="https://example.test/superset/sqllab/"),
            )

        def build_requests_session(self, cookies):
            return SimpleNamespace(cookies=cookies)

    class FakeUiRunner:
        def __init__(self, **kwargs) -> None:
            pass

        def run_query(self, sql: str) -> QueryResult:
            run_calls.append(sql)
            # Fail twice with bot block → refresh then re-login; third succeeds.
            if len(run_calls) <= 2:
                raise RuntimeError(
                    "WAF bot block (BOT-99): SQL Lab execute returned HTML "
                    "(not session logout; url=/api/v1/sqllab/execute/)"
                )
            return QueryResult(
                dataframe=pd.DataFrame([{"assignment_id": "ok"}]),
                metadata={"row_count": 1},
                source="ui",
            )

    monkeypatch.setattr(query_runner_module, "SupersetAuthBootstrap", FakeAuth)
    monkeypatch.setattr(query_runner_module, "SupersetUiRunner", FakeUiRunner)
    monkeypatch.setattr(query_runner_module.time, "sleep", lambda _s: None)

    runner = SupersetQueryRunner(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        mode="ui",
        max_bot_recoveries=3,
        bot_recovery_backoff_seconds=(0.0, 0.0, 0.0),
    )
    monkeypatch.setattr(runner, "_page_looks_logged_out", lambda: False)
    monkeypatch.setattr(
        runner,
        "_force_refresh_sql_lab",
        lambda: refresh_calls.__setitem__("n", refresh_calls["n"] + 1),
    )
    monkeypatch.setattr(
        runner,
        "_relogin",
        lambda: relogin_calls.__setitem__("n", relogin_calls["n"] + 1),
    )

    frame = runner("SELECT 1")
    assert frame.to_dict(orient="records") == [{"assignment_id": "ok"}]
    assert refresh_calls["n"] == 1
    assert relogin_calls["n"] == 1
    assert len(run_calls) == 3

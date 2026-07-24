from __future__ import annotations

import pandas as pd
import pytest

import app.engine.superset_ui_runner as ui_runner_module
from app.engine.superset_ui_runner import SupersetUiRunner


class FakeKeyboard:
    def press(self, key: str) -> None:
        return None


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.test/superset/sqllab/"
        self.keyboard = FakeKeyboard()
        self.response_callbacks = []
        self.editor_sql = ""
        self.focused_ace = False

    def on(self, event_name: str, callback):
        self.response_callbacks.append((event_name, callback))

    def goto(self, url: str, wait_until: str) -> None:
        self.url = url

    def fill(self, selector: str, value: str) -> None:
        self.editor_sql = value
        return None

    def click(self, selector: str) -> None:
        if selector == ".ace_editor":
            self.focused_ace = True
        return None

    def wait_for_timeout(self, timeout: int) -> None:
        return None

    def wait_for_load_state(self, state: str) -> None:
        return None

    def wait_for_function(self, script: str, arg=None, timeout=None) -> None:
        return None

    def evaluate(self, script: str, arg=None):
        if "visibleAceCount" in script:
            return {"visibleAceCount": 1, "hasFocusedAce": self.focused_ace}
        if "textarea.value" in script or "editor.getValue" in script or "contentEditable" in script:
            return self.editor_sql if self.focused_ace else "SELECT * FROM old"
        return []

    def eval_on_selector(self, selector: str, script: str, arg=None):
        if arg is not None:
            self.editor_sql = arg
        return None

    def content(self) -> str:
        return ""

    def screenshot(self, path: str) -> None:
        return None


class FakeProbe:
    def attach(self, page: FakePage) -> "FakeProbe":
        return self

    def candidate_summaries(self) -> list[str]:
        return []


class FakeRequest:
    def __init__(self, method: str = "POST", resource_type: str = "xhr") -> None:
        self.method = method
        self.resource_type = resource_type


class FakeResponse:
    def __init__(self, url: str, payload: object, text: str | None = None) -> None:
        self.url = url
        self.request = FakeRequest()
        self._payload = payload
        self._text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def text(self) -> str:
        if self._text is not None:
            return self._text
        if isinstance(self._payload, Exception):
            raise self._payload
        return str(self._payload)


def test_ui_runner_ignores_stale_visible_rows_until_results_change(monkeypatch) -> None:
    page = FakePage()
    visible_rows_sequence = [
        [{"KODE_KAB": "71", "NKS": "old"}],
        [{"KODE_KAB": "71", "NKS": "old"}],
        [{"KODE_KAB": "01", "NKS": "new"}],
    ]

    def fake_visible_result_rows(_page):
        return visible_rows_sequence.pop(0)

    monkeypatch.setattr(ui_runner_module, "snapshot_visible_result_rows", fake_visible_result_rows)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", fake_visible_result_rows)
    monkeypatch.setattr(ui_runner_module, "fill_sql_editor", lambda page, sql: None)
    monkeypatch.setattr(ui_runner_module, "click_run_query", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1, 1),
    )

    result = runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")

    assert isinstance(result.dataframe, pd.DataFrame)
    assert result.dataframe.to_dict(orient="records") == [{"KODE_KAB": "01", "NKS": "new"}]


def test_ui_runner_verifies_editor_sql_before_accepting_result(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM old"

    def fake_fill_sql_editor(_page, sql: str) -> None:
        # Simulate broken editor update that leaves old SQL in place.
        return None

    monkeypatch.setattr(ui_runner_module, "fill_sql_editor", fake_fill_sql_editor)
    monkeypatch.setattr(ui_runner_module, "click_run_query", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "snapshot_visible_result_rows", lambda page: [{"KODE_KAB": "71", "NKS": "old"}])
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: [{"KODE_KAB": "71", "NKS": "old"}])
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
    )

    with pytest.raises(RuntimeError, match="Editor SQL did not update"):
        runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")


def test_ui_runner_focuses_visible_ace_editor_before_readback(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='01'"

    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: [{"KODE_KAB": "01", "NKS": "new"}])
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)
    monkeypatch.setattr(ui_runner_module, "click_run_query", lambda page: None)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
    )

    result = runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")

    assert page.focused_ace is True
    assert result.dataframe.to_dict(orient="records") == [{"KODE_KAB": "01", "NKS": "new"}]


def test_ui_runner_surfaces_execute_response_error_payload(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='01'"
    emitted_callbacks = []

    def on(event_name: str, callback):
        emitted_callbacks.append((event_name, callback))

    page.on = on

    def fake_click_run_query(_page):
        for event_name, callback in emitted_callbacks:
            if event_name == "response":
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/execute/",
                        {"errors": [{"message": "can not access the query"}]},
                    )
                )

    monkeypatch.setattr(ui_runner_module, "click_run_query", fake_click_run_query)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
    )

    with pytest.raises(RuntimeError, match="can not access the query"):
        runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")


def test_ui_runner_reports_pending_execute_query_metadata_when_no_results_arrive(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='01'"
    emitted_callbacks = []

    def on(event_name: str, callback):
        emitted_callbacks.append((event_name, callback))

    page.on = on

    def fake_click_run_query(_page):
        for event_name, callback in emitted_callbacks:
            if event_name == "response":
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/execute/",
                        {"query": {"queryId": 1463098, "serverId": 1463098, "state": "pending", "resultsKey": None}},
                    )
                )

    monkeypatch.setattr(ui_runner_module, "click_run_query", fake_click_run_query)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
        pending_wait_timeout_ms=0,
    )

    with pytest.raises(RuntimeError, match="queryId=1463098.*state=pending"):
        runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")


def test_ui_runner_waits_for_running_query_until_results_arrive(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='01'"
    emitted_callbacks = []
    tick = {"n": 0}

    def on(event_name: str, callback):
        emitted_callbacks.append((event_name, callback))

    page.on = on

    def fake_click_run_query(_page):
        for event_name, callback in emitted_callbacks:
            if event_name == "response":
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/execute/",
                        {
                            "query": {
                                "queryId": 1678560,
                                "serverId": 1678560,
                                "state": "running",
                                "resultsKey": None,
                                "sql": "SELECT * FROM foo WHERE art.level_2_code='01'",
                            }
                        },
                    )
                )

    def fake_wait_for_timeout(_ms: int) -> None:
        tick["n"] += 1
        # After a few pending polls, deliver results for the running query.
        if tick["n"] < 3:
            return
        for event_name, callback in emitted_callbacks:
            if event_name != "response":
                continue
            callback(
                FakeResponse(
                    "https://example.test/api/v1/query/updated_since",
                    {
                        "result": [
                            {
                                "queryId": 1678560,
                                "serverId": 1678560,
                                "state": "success",
                                "resultsKey": "late-key",
                                "sql": "SELECT * FROM foo WHERE art.level_2_code='01'",
                            }
                        ]
                    },
                )
            )
            callback(
                FakeResponse(
                    "https://example.test/api/v1/sqllab/results/?q=(key:'late-key')",
                    {"query_id": 1678560, "data": [{"KODE_KAB": "01", "NKS": "late"}]},
                )
            )

    page.wait_for_timeout = fake_wait_for_timeout
    monkeypatch.setattr(ui_runner_module, "click_run_query", fake_click_run_query)
    monkeypatch.setattr(ui_runner_module, "snapshot_visible_result_rows", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
        pending_wait_timeout_ms=10_000,
        response_poll_interval_ms=1,
    )

    result = runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")
    assert result.dataframe.to_dict(orient="records") == [{"KODE_KAB": "01", "NKS": "late"}]


def test_detect_waf_bot_block_extracts_bot_id() -> None:
    html = "<html><body>Bot Detected<br/>BOT-12236185493749556630</body></html>"
    assert ui_runner_module.detect_waf_bot_block(html) == "WAF bot block (BOT-12236185493749556630)"
    assert ui_runner_module.detect_waf_bot_block('{"query":{}}') is None


def test_ui_runner_surfaces_bot_block_and_ignores_stop_html(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='01'"
    emitted_callbacks = []
    wait_ticks = {"n": 0}

    def on(event_name: str, callback):
        emitted_callbacks.append((event_name, callback))

    page.on = on

    bot_html = "<html><body>Bot Detected BOT-12236185493749556630</body></html>"

    def fake_click_run_query(_page):
        for event_name, callback in emitted_callbacks:
            if event_name != "response":
                continue
            callback(
                FakeResponse(
                    "https://example.test/api/v1/sqllab/execute/",
                    Exception("Unexpected token '<'"),
                    text=bot_html,
                )
            )
            # SPA auto-stop after blocked execute — must not overwrite bot error.
            callback(
                FakeResponse(
                    "https://example.test/api/v1/query/stop",
                    Exception("Unexpected token '<'"),
                    text=bot_html,
                )
            )

    def fake_wait_for_timeout(_ms: int) -> None:
        wait_ticks["n"] += 1

    page.wait_for_timeout = fake_wait_for_timeout
    monkeypatch.setattr(ui_runner_module, "click_run_query", fake_click_run_query)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "snapshot_visible_result_rows", lambda page: None)
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1, 1, 1, 1, 1),
        pending_wait_timeout_ms=0,
    )

    with pytest.raises(RuntimeError, match=r"WAF bot block \(BOT-12236185493749556630\)"):
        runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")
    # Editor settle uses wait_for_timeout once; response poll loop must not run.
    assert wait_ticks["n"] == 1


def test_ui_runner_uses_results_key_response_as_source_of_truth(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='01'"
    emitted_callbacks = []

    def on(event_name: str, callback):
        emitted_callbacks.append((event_name, callback))

    page.on = on

    def fake_click_run_query(_page):
        for event_name, callback in emitted_callbacks:
            if event_name == "response":
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/execute/",
                        {
                            "query": {
                                "queryId": 1463116,
                                "serverId": 1463116,
                                "state": "success",
                                "resultsKey": "abc-key",
                            }
                        },
                    )
                )
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/results/?q=(key:'abc-key',rows:10000)",
                        {
                            "status": "success",
                            "data": [{"KODE_KAB": "01", "NKS": "new"}],
                            "columns": [{"name": "KODE_KAB"}, {"name": "NKS"}],
                        },
                    )
                )

    monkeypatch.setattr(ui_runner_module, "click_run_query", fake_click_run_query)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: [{"KODE_KAB": "71", "NKS": "old"}])
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
    )

    result = runner.run_query("SELECT * FROM foo WHERE art.level_2_code='01'")

    assert result.dataframe.to_dict(orient="records") == [{"KODE_KAB": "01", "NKS": "new"}]


def test_ui_runner_rejects_results_payload_with_mismatched_query_id(monkeypatch) -> None:
    page = FakePage()
    page.editor_sql = "SELECT * FROM foo WHERE art.level_2_code='02'"
    emitted_callbacks = []

    def on(event_name: str, callback):
        emitted_callbacks.append((event_name, callback))

    page.on = on

    def fake_click_run_query(_page):
        for event_name, callback in emitted_callbacks:
            if event_name == "response":
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/execute/",
                        {
                            "query": {
                                "queryId": 200,
                                "serverId": 200,
                                "state": "success",
                                "resultsKey": "key-200",
                            }
                        },
                    )
                )
                callback(
                    FakeResponse(
                        "https://example.test/api/v1/sqllab/results/?q=(key:'old-key',rows:10000)",
                        {
                            "status": "success",
                            "query_id": 199,
                            "data": [{"KODE_KAB": "01", "NKS": "old"}],
                        },
                    )
                )

    monkeypatch.setattr(ui_runner_module, "click_run_query", fake_click_run_query)
    monkeypatch.setattr(ui_runner_module, "capture_visible_result_rows", lambda page: [{"KODE_KAB": "01", "NKS": "old"}])
    monkeypatch.setattr(ui_runner_module, "SupersetNetworkProbe", FakeProbe)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        page=page,
        response_wait_intervals_ms=(1,),
    )

    with pytest.raises(RuntimeError, match="resultsKey=key-200"):
        runner.run_query("SELECT * FROM foo WHERE art.level_2_code='02'")

def test_ui_runner_self_launch_uses_browser_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    launch_calls: list[dict[str, object]] = []
    closed: list[object] = []

    class FakePage:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}
            self.goto_calls: list[tuple[str, str]] = []
            self.url = "https://example.test/superset/sqllab/"

        def on(self, event: str, handler) -> None:
            self.handlers[event] = handler

        def remove_listener(self, event: str, handler) -> None:
            self.handlers.pop(event, None)

        def goto(self, url: str, wait_until: str = "load") -> None:
            self.goto_calls.append((url, wait_until))

        def wait_for_timeout(self, ms: int) -> None:
            return None

        def wait_for_selector(self, selector: str, timeout: int = 0):
            raise AssertionError("should not reach editor path in this test")

        def wait_for_function(self, script: str, arg: object | None = None) -> None:
            raise AssertionError("should not wait for SQL Lab markers in this test")

        def evaluate(self, script: str, arg: object | None = None) -> object:
            return []

        def content(self) -> str:
            return "<html></html>"

        def screenshot(self, path: str) -> None:
            return None

    class FakeContext:
        def __init__(self) -> None:
            self.pages: list[FakePage] = []
            self.added_cookies: list[list[dict[str, object]]] = []
            self.closed = False

        def new_page(self) -> FakePage:
            page = FakePage()
            self.pages.append(page)
            return page

        def add_cookies(self, cookies: list[dict[str, object]]) -> None:
            self.added_cookies.append(cookies)

        def close(self) -> None:
            self.closed = True

    class FakeSession:
        def __init__(self) -> None:
            self.manager = object()
            self.context = FakeContext()
            self.browser = None

    def fake_launch(**kwargs: object) -> FakeSession:
        launch_calls.append(kwargs)
        return FakeSession()

    def fake_close(session: FakeSession) -> None:
        closed.append(session)
        session.context.close()

    monkeypatch.setattr(ui_runner_module, "launch_stealth_browser", fake_launch)
    monkeypatch.setattr(ui_runner_module, "close_browser_session", fake_close)

    runner = SupersetUiRunner(
        sql_lab_url="https://example.test/superset/sqllab/",
        auth_cookies=[{"name": "session", "value": "abc", "domain": "example.test", "path": "/"}],
        response_wait_intervals_ms=(1,),
    )

    try:
        runner.run_query("select 1")
    except Exception:
        # expected: fails later at editor selectors; launch must already have happened
        pass

    assert launch_calls
    assert launch_calls[0]["headless"] is False
    assert closed


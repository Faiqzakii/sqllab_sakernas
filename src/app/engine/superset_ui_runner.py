from __future__ import annotations

from collections.abc import Iterable
import re
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlparse

import pandas as pd

from app.engine.superset_auth import cookie_matches_base_url, sanitize_url
from app.engine.superset_client import QueryResult, normalize_sql_json_to_dataframe
from app.engine.superset_probe_support import SupersetNetworkProbe, is_sql_lab_candidate

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
except ImportError:
    _sync_playwright = None


sync_playwright = _sync_playwright

DEFAULT_UI_RESPONSE_WAIT_TIMEOUT_MS = 30_000
DEFAULT_UI_RESPONSE_POLL_INTERVAL_MS = 1_000
WELCOME_TO_SQL_LAB_LINK_SELECTOR = 'a[href="/superset/sqllab/"]'
SQL_LAB_RESULT_TABLE_XPATH = "/html/body/div[2]/div/div/div[2]/div/div[126]/div/div[3]/div[3]/div/div[2]/div/div[1]/div/div[3]/div/div/div/div/div/div/div/div[1]/table"
SQL_LAB_EDITOR_FOCUS_XPATH = "/html/body/div[2]/div/div/div[2]/div/div[126]/div/div[3]/div[3]/div/div[2]/div/div[1]/div/div[2]/span"
SQL_LAB_EDITOR_SELECTORS = ("textarea", '[role="textbox"]', f"xpath={SQL_LAB_EDITOR_FOCUS_XPATH}")
SQL_LAB_RUN_SELECTORS = (
    "button:has-text('Run')",
    "button:has-text('RUN')",
    "button[aria-label='Run']",
)
SQL_LAB_RESULT_READY_TEXT = "rows returned"
SQL_EDITOR_SETTLE_DELAY_MS = 500
SQL_BATCH_SETTLE_DELAY_MS = 1000


def build_wait_intervals(total_timeout_ms: int, poll_interval_ms: int) -> tuple[int, ...]:
    if total_timeout_ms <= 0:
        raise ValueError("total_timeout_ms must be positive")
    if poll_interval_ms <= 0:
        raise ValueError("poll_interval_ms must be positive")

    interval_count, remainder = divmod(total_timeout_ms, poll_interval_ms)
    intervals = [poll_interval_ms] * interval_count
    if remainder > 0:
        intervals.append(remainder)
    return tuple(intervals)


class BrowserKeyboard(Protocol):
    def press(self, key: str) -> None: ...


class BrowserPage(Protocol):
    def on(self, event_name: str, callback: Any) -> None: ...
    def goto(self, url: str, wait_until: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def wait_for_timeout(self, timeout: int) -> None: ...
    def wait_for_load_state(self, state: str) -> None: ...
    def wait_for_function(self, script: str, arg: object | None = None) -> None: ...
    def evaluate(self, script: str, arg: object | None = None) -> object: ...
    def eval_on_selector(self, selector: str, script: str, arg: object | None = None) -> object: ...
    def content(self) -> str: ...
    def screenshot(self, path: str) -> None: ...
    keyboard: BrowserKeyboard
    url: str


class BrowserContext(Protocol):
    def add_cookies(self, cookies: list[dict[str, Any]]) -> None: ...
    def new_page(self) -> BrowserPage: ...
    def close(self) -> None: ...


class BrowserInstance(Protocol):
    def new_context(self) -> BrowserContext: ...
    def new_page(self) -> BrowserPage: ...
    def close(self) -> None: ...


class ChromiumLauncher(Protocol):
    def launch(self, headless: bool) -> BrowserInstance: ...


class PlaywrightInstance(Protocol):
    chromium: ChromiumLauncher


class PlaywrightContextManager(Protocol):
    def __enter__(self) -> PlaywrightInstance: ...
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...


class BrowserRequest(Protocol):
    resource_type: str
    method: str


class BrowserResponse(Protocol):
    url: str
    request: BrowserRequest

    def json(self) -> object: ...


def rows_to_dataframe(rows: object) -> pd.DataFrame:
    if not isinstance(rows, list):
        return pd.DataFrame()
    normalized_rows = [row for row in rows if isinstance(row, dict)]
    if len(normalized_rows) != len(rows):
        return pd.DataFrame()
    return pd.DataFrame(normalized_rows)


def is_likely_sql_lab_result_response(response: BrowserResponse) -> bool:
    response_url = getattr(response, "url", "")
    normalized_url = response_url.lower()
    is_execute_endpoint = "/api/v1/sqllab/execute/" in normalized_url
    is_results_endpoint = "/api/v1/sqllab/results/" in normalized_url
    is_updated_since_endpoint = "/api/v1/query/updated_since" in normalized_url
    if not is_execute_endpoint and not is_results_endpoint and not is_updated_since_endpoint and not is_sql_lab_candidate(normalized_url):
        return False
    if not is_execute_endpoint and not is_updated_since_endpoint and "/results/" not in normalized_url:
        return False

    request = getattr(response, "request", None)
    if request is None:
        return True
    resource_type = getattr(request, "resource_type", "")
    if resource_type not in {"xhr", "fetch", ""}:
        return False
    method = getattr(request, "method", "GET").upper()
    return method in {"GET", "POST"}


def filter_auth_cookies_for_url(auth_cookies: list[dict[str, Any]], url: str) -> list[dict[str, Any]]:
    if not urlparse(url).hostname:
        return []
    return [cookie for cookie in auth_cookies if cookie_matches_base_url(cookie, url)]


def wait_for_sql_lab_link_marker(page: BrowserPage) -> None:
    page.wait_for_function(
        "(selector) => { const node = document.querySelector(selector); return node instanceof HTMLAnchorElement && node.getAttribute('href') === '/superset/sqllab/' && node.textContent !== null && node.textContent.includes('SQL Lab'); }",
        WELCOME_TO_SQL_LAB_LINK_SELECTOR,
    )


def wait_for_sql_lab_page(page: BrowserPage) -> None:
    page.wait_for_function(
        "(expectedUrlPart) => window.location.pathname.includes(expectedUrlPart)",
        "/superset/sqllab/",
    )


def wait_for_sql_lab_result_ready_marker(page: BrowserPage) -> None:
    page.wait_for_function(
        "(expectedText) => document.body.textContent !== null && document.body.textContent.includes(expectedText)",
        SQL_LAB_RESULT_READY_TEXT,
    )


def read_sql_lab_result_table(page: BrowserPage) -> pd.DataFrame:
    rows = page.evaluate(
        "({ table_xpath }) => { const table = document.evaluate(table_xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; if (!(table instanceof HTMLTableElement)) { return []; } const bodyRows = Array.from(table.querySelectorAll('tbody tr')); if (bodyRows.length === 0) { return []; } const headerCells = Array.from(table.querySelectorAll('thead th')).map((cell) => cell.textContent?.trim() ?? ''); return bodyRows.map((row) => { const cells = Array.from(row.querySelectorAll('td')); return Object.fromEntries(cells.map((cell, index) => [headerCells[index] || `column_${index}`, cell.textContent?.trim() ?? ''])); }); }",
        {"table_xpath": SQL_LAB_RESULT_TABLE_XPATH},
    )
    return rows_to_dataframe(rows)


def capture_visible_result_rows(page: BrowserPage) -> list[dict[str, Any]] | None:
    try:
        wait_for_sql_lab_result_ready_marker(page)
    except Exception:
        pass
    table_dataframe = read_sql_lab_result_table(page)
    if table_dataframe.empty:
        return None
    return cast(list[dict[str, Any]], table_dataframe.to_dict(orient="records"))


def rows_signature(rows: list[dict[str, Any]] | None) -> str | None:
    if rows is None:
        return None
    return repr(rows)


def normalize_sql_text(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower()


def extract_results_key_from_url(url: str) -> str | None:
    decoded_url = unquote(url)
    match = re.search(r"key:('?)([^',)]+)\1", decoded_url)
    if match:
        return match.group(2)
    return None


def read_editor_sql(page: BrowserPage) -> str | None:
    try:
        editor_value = page.evaluate(
            "() => { const visibleAceEditors = Array.from(document.querySelectorAll('.ace_editor')).filter((node) => node instanceof HTMLElement && node.offsetParent !== null); if (visibleAceEditors.length > 0 && window.ace && typeof window.ace.edit === 'function') { const activeAceEditor = visibleAceEditors[visibleAceEditors.length - 1]; const editor = window.ace.edit(activeAceEditor); if (editor && typeof editor.getValue === 'function') { return editor.getValue(); } } const textarea = document.querySelector('textarea'); if (textarea instanceof HTMLTextAreaElement) { return textarea.value; } const textbox = document.querySelector('[role=\"textbox\"]'); if (textbox instanceof HTMLTextAreaElement) { return textbox.value; } if (textbox instanceof HTMLElement && textbox.isContentEditable) { return textbox.textContent || ''; } return null; }"
        )
    except Exception:
        return None
    return editor_value if isinstance(editor_value, str) else None


def wait_for_visible_ace_editor(page: BrowserPage) -> None:
    page.wait_for_function(
        "() => Array.from(document.querySelectorAll('.ace_editor')).some(node => node instanceof HTMLElement && node.offsetParent !== null)"
    )


def focus_visible_ace_editor(page: BrowserPage) -> None:
    try:
        page.click(".ace_editor")
    except Exception:
        return


def fill_sql_editor(page: BrowserPage, sql: str) -> None:
    last_error: Exception | None = None
    try:
        wait_for_visible_ace_editor(page)
        focus_visible_ace_editor(page)
        page.evaluate(
            "(value) => { const visibleAceEditors = Array.from(document.querySelectorAll('.ace_editor')).filter((node) => node instanceof HTMLElement && node.offsetParent !== null); if (visibleAceEditors.length > 0 && window.ace && typeof window.ace.edit === 'function') { const activeAceEditor = visibleAceEditors[visibleAceEditors.length - 1]; const editor = window.ace.edit(activeAceEditor); if (!editor || typeof editor.setValue !== 'function' || typeof editor.getValue !== 'function') { throw new Error('Active Ace editor is not writable'); } editor.focus(); editor.setValue(value, -1); editor.clearSelection(); editor.moveCursorTo(0, 0); activeAceEditor.dispatchEvent(new Event('input', { bubbles: true })); activeAceEditor.dispatchEvent(new Event('change', { bubbles: true })); return 'ace'; } return null; }",
            sql,
        )
        return
    except Exception as exc:
        last_error = exc

    for selector in SQL_LAB_EDITOR_SELECTORS:
        try:
            page.click(selector)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            page.fill(selector, sql)
            return
        except Exception as exc:
            last_error = exc
            try:
                page.eval_on_selector(
                    selector,
                    "(node, value) => { const editorElement = node instanceof HTMLElement ? node : null; if (!editorElement) { throw new Error('Editor element not found'); } const aceEditor = window.ace && typeof window.ace.edit === 'function' ? window.ace.edit(editorElement.closest('.ace_editor') || editorElement) : null; if (aceEditor && typeof aceEditor.setValue === 'function') { aceEditor.setValue(value, -1); aceEditor.clearSelection(); editorElement.dispatchEvent(new Event('input', { bubbles: true })); editorElement.dispatchEvent(new Event('change', { bubbles: true })); return 'ace'; } if (editorElement.isContentEditable) { editorElement.textContent = value; editorElement.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' })); editorElement.dispatchEvent(new Event('change', { bubbles: true })); return 'contenteditable'; } throw new Error('Unsupported editor surface'); }",
                    sql,
                )
                return
            except Exception as fallback_exc:
                last_error = fallback_exc
                pass
    if last_error is not None:
        raise last_error
    raise RuntimeError("No SQL editor selectors are configured")


def click_run_query(page: BrowserPage) -> None:
    last_error: Exception | None = None
    for selector in SQL_LAB_RUN_SELECTORS:
        try:
            page.click(selector)
            return
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError("No SQL Lab run selectors are configured")


class SupersetUiRunner:
    def __init__(
        self,
        sql_lab_url: str,
        auth_cookies: list[dict[str, Any]] | None = None,
        headless: bool = True,
        response_wait_intervals_ms: Iterable[int] | None = None,
        response_wait_timeout_ms: int = DEFAULT_UI_RESPONSE_WAIT_TIMEOUT_MS,
        response_poll_interval_ms: int = DEFAULT_UI_RESPONSE_POLL_INTERVAL_MS,
        browser: BrowserInstance | None = None,
        context: BrowserContext | None = None,
        page: BrowserPage | None = None,
        debug_callback: Any | None = None,
    ) -> None:
        self.sql_lab_url = sql_lab_url
        self.auth_cookies = filter_auth_cookies_for_url(list(auth_cookies or []), sql_lab_url)
        self.headless = headless
        self.browser = browser
        self.context = context
        self.page = page
        self.debug_callback = debug_callback
        self.response_wait_intervals_ms = (
            tuple(response_wait_intervals_ms)
            if response_wait_intervals_ms is not None
            else build_wait_intervals(
                total_timeout_ms=response_wait_timeout_ms,
                poll_interval_ms=response_poll_interval_ms,
            )
        )

    def _emit_debug(self, stage: str, **payload: Any) -> None:
        if self.debug_callback is None:
            return
        self.debug_callback({"stage": stage, **payload})

    def save_failure_artifacts(self, artifact_path: str) -> None:
        if self.page is None:
            return
        artifact_base_path = Path(artifact_path)
        artifact_base_path.parent.mkdir(parents=True, exist_ok=True)
        html_path = artifact_base_path.with_suffix(".sqllab.html")
        screenshot_path = artifact_base_path.with_suffix(".sqllab.png")
        html_path.write_text(self.page.content(), encoding="utf-8")
        self.page.screenshot(path=str(screenshot_path))

    def run_query(self, sql: str) -> QueryResult:
        if sync_playwright is None and self.page is None:
            raise RuntimeError("Playwright is required for UI fallback execution")

        payload_rows: list[dict[str, Any]] | None = None
        captured_empty_result = False
        editor_error: Exception | None = None
        execute_error: Exception | None = None
        execute_pending_query: dict[str, Any] | None = None
        execute_success_query: dict[str, Any] | None = None
        active_query_row: dict[str, Any] | None = None
        accepted_via_network = False
        run_clicked = False
        managed_playwright_context: PlaywrightContextManager | None = None
        managed_browser: BrowserInstance | None = None
        managed_context: BrowserContext | None = None
        try:
            if self.page is not None:
                page = self.page
            elif self.context is not None:
                page = self.context.new_page()
            else:
                managed_playwright_context = cast(PlaywrightContextManager, sync_playwright())
                playwright = managed_playwright_context.__enter__()
                managed_browser = playwright.chromium.launch(headless=self.headless)
                if self.auth_cookies:
                    managed_context = managed_browser.new_context()
                    managed_context.add_cookies(self.auth_cookies)
                    page = managed_context.new_page()
                else:
                    page = managed_browser.new_page()

            network_probe = SupersetNetworkProbe().attach(page)
            self._emit_debug("page.ready", current_url=sanitize_url(str(getattr(page, "url", self.sql_lab_url))))
            previous_visible_rows = capture_visible_result_rows(page)
            previous_visible_signature = rows_signature(previous_visible_rows)
            self._emit_debug("result.previous_visible", signature=previous_visible_signature)

            def capture_response(response: BrowserResponse) -> None:
                nonlocal payload_rows, captured_empty_result, accepted_via_network, execute_error, execute_pending_query, execute_success_query, active_query_row, run_clicked
                if payload_rows is not None or captured_empty_result:
                    return
                if not is_likely_sql_lab_result_response(response):
                    return
                response_url = getattr(response, "url", "").lower()
                self._emit_debug("response.candidate", url=response_url)
                if not run_clicked and "/api/v1/sqllab/results/" in response_url:
                    self._emit_debug("results.rejected_pre_click", url=response_url)
                    return
                if "/api/v1/sqllab/results/" in response_url and execute_success_query is None and active_query_row is None:
                    self._emit_debug("results.rejected_before_active_query", url=response_url)
                    return
                try:
                    payload = response.json()
                except Exception:
                    self._emit_debug("response.unreadable", url=response_url)
                    return
                if "/api/v1/sqllab/execute/" in response_url and isinstance(payload, dict):
                    self._emit_debug("execute.response", payload=payload)
                    errors = payload.get("errors")
                    if isinstance(errors, list) and errors:
                        first_error = errors[0]
                        if isinstance(first_error, dict):
                            message = first_error.get("message") or first_error.get("error") or str(first_error)
                        else:
                            message = str(first_error)
                        execute_error = RuntimeError(str(message))
                        self._emit_debug("execute.error", message=str(message))
                        return
                    query_payload = payload.get("query")
                    if isinstance(query_payload, dict):
                        if normalize_sql_text(str(query_payload.get("sql", ""))) != normalize_sql_text(sql):
                            self._emit_debug("execute.rejected_sql_mismatch", query=query_payload)
                            return
                        if query_payload.get("resultsKey"):
                            execute_success_query = query_payload
                            self._emit_debug("execute.success_metadata", query=query_payload)
                        else:
                            execute_pending_query = query_payload
                            self._emit_debug("execute.pending_metadata", query=query_payload)
                if "/api/v1/query/updated_since" in response_url and isinstance(payload, dict):
                    result_rows = payload.get("result")
                    if isinstance(result_rows, list):
                        for row in result_rows:
                            if not isinstance(row, dict):
                                continue
                            if normalize_sql_text(str(row.get("sql", ""))) != normalize_sql_text(sql):
                                self._emit_debug("updated_since.rejected_sql_mismatch", row=row)
                                continue
                            active_query_row = row
                            self._emit_debug("updated_since.accepted", row=row)
                            if row.get("resultsKey"):
                                execute_success_query = row
                            else:
                                execute_pending_query = row
                            break
                if isinstance(payload, list):
                    dataframe = rows_to_dataframe(payload)
                    if dataframe.empty:
                        if payload == []:
                            captured_empty_result = True
                        return
                    payload_rows = cast(list[dict[str, Any]], dataframe.to_dict(orient="records"))
                    accepted_via_network = True
                    self._emit_debug("response.accepted_list", row_count=len(payload_rows))
                    return
                if isinstance(payload, dict):
                    data_rows = payload.get("data")
                    if isinstance(data_rows, list):
                        expected_query_id = None
                        expected_server_id = None
                        expected_results_key = None
                        if active_query_row is not None:
                            expected_query_id = active_query_row.get("query_id") or active_query_row.get("queryId")
                            expected_server_id = active_query_row.get("server_id") or active_query_row.get("serverId")
                            expected_results_key = active_query_row.get("results_key") or active_query_row.get("resultsKey")
                        elif execute_success_query is not None:
                            expected_query_id = execute_success_query.get("queryId")
                            expected_server_id = execute_success_query.get("serverId")
                            expected_results_key = execute_success_query.get("resultsKey")

                        response_results_key = extract_results_key_from_url(response_url)

                        if expected_results_key is not None and response_results_key != expected_results_key:
                            self._emit_debug(
                                "results.rejected_results_key_mismatch",
                                response_results_key=response_results_key,
                                expected_results_key=expected_results_key,
                            )
                            return

                        if expected_query_id is not None and payload.get("query_id") not in {
                            expected_query_id,
                            expected_server_id,
                        }:
                            self._emit_debug(
                                "results.rejected_mismatch",
                                query_id=payload.get("query_id"),
                                expected_query_id=expected_query_id,
                                expected_server_id=expected_server_id,
                            )
                            return
                        dataframe = rows_to_dataframe(data_rows)
                        if dataframe.empty:
                            return
                        payload_rows = cast(list[dict[str, Any]], dataframe.to_dict(orient="records"))
                        accepted_via_network = True
                        self._emit_debug(
                            "results.accepted",
                            query_id=payload.get("query_id"),
                            row_count=len(payload_rows),
                        )
                        return

            page.on("response", capture_response)
            page_url = sanitize_url(str(getattr(page, "url", self.sql_lab_url)))
            if page_url.rstrip("/").endswith("/superset/welcome"):
                wait_for_sql_lab_link_marker(page)
                page.click(WELCOME_TO_SQL_LAB_LINK_SELECTOR)
                page.wait_for_load_state("domcontentloaded")
                wait_for_sql_lab_page(page)
            else:
                page.goto(self.sql_lab_url, wait_until="domcontentloaded")
            try:
                self._emit_debug("editor.fill.start")
                fill_sql_editor(page, sql)
                self._emit_debug("editor.fill.done")
                editor_sql = read_editor_sql(page)
                self._emit_debug("editor.readback", matches=(editor_sql.strip() == sql.strip()) if editor_sql is not None else None)
                if editor_sql is not None and editor_sql.strip() != sql.strip():
                    raise RuntimeError("Editor SQL did not update to the requested batch query")
                page.wait_for_timeout(SQL_EDITOR_SETTLE_DELAY_MS)
                self._emit_debug("editor.settled", delay_ms=SQL_EDITOR_SETTLE_DELAY_MS)
                click_run_query(page)
                run_clicked = True
                self._emit_debug("query.run_clicked")
            except Exception as exc:
                editor_error = exc
                self._emit_debug("editor.error", error=repr(exc))
                if execute_success_query is None and active_query_row is None and execute_pending_query is None:
                    visible_rows = capture_visible_result_rows(page)
                    if rows_signature(visible_rows) != previous_visible_signature:
                        payload_rows = visible_rows

            for wait_interval_ms in self.response_wait_intervals_ms:
                if payload_rows is not None or captured_empty_result:
                    break
                page.wait_for_timeout(wait_interval_ms)
                self._emit_debug("results.wait_tick", wait_ms=wait_interval_ms)
                if execute_success_query is None and active_query_row is None and execute_pending_query is None:
                    visible_rows = capture_visible_result_rows(page)
                    if rows_signature(visible_rows) != previous_visible_signature:
                        payload_rows = visible_rows
                        self._emit_debug("results.accepted_visible", row_count=len(payload_rows))
        finally:
            try:
                if managed_context is not None:
                    managed_context.close()
            finally:
                try:
                    if managed_browser is not None:
                        managed_browser.close()
                finally:
                    if managed_playwright_context is not None:
                        managed_playwright_context.__exit__(None, None, None)

        if payload_rows is None and not captured_empty_result:
            current_page = sanitize_url(str(getattr(page, "url", self.sql_lab_url)))
            sql_lab_candidates = network_probe.candidate_summaries()
            if execute_error is not None:
                raise RuntimeError(
                    f"{execute_error} (current_page={current_page}, sql_lab_candidates={sql_lab_candidates})"
                ) from execute_error
            if execute_pending_query is not None:
                raise RuntimeError(
                    "SQL Lab execute returned pending query metadata "
                    f"(queryId={execute_pending_query.get('queryId')}, serverId={execute_pending_query.get('serverId')}, "
                    f"state={execute_pending_query.get('state')}, resultsKey={execute_pending_query.get('resultsKey')}, "
                    f"current_page={current_page}, sql_lab_candidates={sql_lab_candidates})"
                )
            if execute_success_query is not None:
                raise RuntimeError(
                    "SQL Lab execute returned success metadata without captured result rows "
                    f"(queryId={execute_success_query.get('queryId')}, serverId={execute_success_query.get('serverId')}, "
                    f"state={execute_success_query.get('state')}, resultsKey={execute_success_query.get('resultsKey')}, "
                    f"current_page={current_page}, sql_lab_candidates={sql_lab_candidates})"
                )
            if editor_error is not None:
                raise RuntimeError(
                    f"{editor_error} (current_page={current_page}, sql_lab_candidates={sql_lab_candidates})"
                ) from editor_error
            raise RuntimeError(
                "No tabular UI result was captured from SQL Lab "
                f"(current_page={current_page}, sql_lab_candidates={sql_lab_candidates})"
            )

        dataframe = pd.DataFrame() if payload_rows is None else rows_to_dataframe(payload_rows)
        if not dataframe.empty:
            page.wait_for_timeout(SQL_BATCH_SETTLE_DELAY_MS)
            self._emit_debug("batch.settle", delay_ms=SQL_BATCH_SETTLE_DELAY_MS, row_count=len(dataframe.index))
        if self.page is None and self.context is not None:
            try:
                page.close()
            except Exception:
                pass
        return QueryResult(
            dataframe=dataframe,
            metadata={"row_count": len(dataframe.index), "mode": "ui_fallback"},
            source="ui",
        )

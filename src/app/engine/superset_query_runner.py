from __future__ import annotations

import time
from typing import Any

import pandas as pd

from app.engine.superset_auth import (
    PAGE_LOAD_TIMEOUT_MS,
    SupersetAuthBootstrap,
    is_login_url,
    navigate_sql_lab,
    sanitize_url,
)
from app.engine.superset_client import SupersetClient
from app.engine.superset_ui_runner import SupersetUiRunner, wait_for_sql_lab_editor_ready


DEFAULT_BOT_RECOVERY_BACKOFF_SECONDS = (5.0, 15.0, 45.0)


def is_bot_block_error(error: BaseException) -> bool:
    message = str(error).lower()
    return "waf bot block" in message or "bot detected" in message


def is_recoverable_session_error(error: BaseException) -> bool:
    if is_bot_block_error(error):
        return True
    message = str(error).lower()
    tokens = (
        "html instead of json",
        "unexpected '<'",
        "unexpected \"<\"",
        "login",
        "unauthorized",
        "401",
        "403",
        "csrf",
        "session expir",
        "possible logout",
        "not authenticated",
        "gateway",
        "bad gateway",
        "service unavailable",
    )
    return any(token in message for token in tokens)


class SupersetQueryRunner:
    """Reusable callable for folder/batch SQL execution against Superset SQL Lab."""

    def __init__(
        self,
        *,
        base_url: str,
        sql_lab_url: str,
        manual_login: bool = False,
        mode: str = "auto",
        debug_callback: Any | None = None,
        max_session_recoveries: int = 1,
        max_bot_recoveries: int = 3,
        bot_recovery_backoff_seconds: tuple[float, ...] = DEFAULT_BOT_RECOVERY_BACKOFF_SECONDS,
    ) -> None:
        self.base_url = base_url
        self.sql_lab_url = sql_lab_url
        self.mode = mode
        self.debug_callback = debug_callback
        self.max_session_recoveries = max(0, int(max_session_recoveries))
        self.max_bot_recoveries = max(0, int(max_bot_recoveries))
        self.bot_recovery_backoff_seconds = tuple(
            max(0.0, float(value)) for value in bot_recovery_backoff_seconds
        ) or DEFAULT_BOT_RECOVERY_BACKOFF_SECONDS
        self.auth = SupersetAuthBootstrap(
            base_url=base_url,
            sql_lab_url=sql_lab_url,
            manual_login=manual_login,
        )
        self.auth_result = self.auth.login_and_capture()
        self.session = self.auth.build_requests_session(self.auth_result.cookies)

    def _emit_debug(self, stage: str, **payload: Any) -> None:
        if self.debug_callback is None:
            return
        self.debug_callback({"stage": stage, **payload})

    def _build_ui_runner(self) -> SupersetUiRunner:
        return SupersetUiRunner(
            sql_lab_url=self.sql_lab_url,
            auth_cookies=self.auth_result.cookies,
            browser=self.auth_result.browser,
            context=self.auth_result.context,
            page=self.auth_result.page,
            debug_callback=self.debug_callback,
        )

    def _run_once(self, sql: str) -> pd.DataFrame:
        ui_runner = self._build_ui_runner()
        if self.mode == "ui":
            result = ui_runner.run_query(sql)
        else:
            client = SupersetClient(
                session=self.session,
                base_url=self.base_url,
                ui_runner=ui_runner,
            )
            result = client.run_query(sql)
        return result.dataframe if result.dataframe is not None else pd.DataFrame()

    def _page_looks_logged_out(self) -> bool:
        page = self.auth_result.page
        if page is None:
            return True
        current_url = sanitize_url(str(getattr(page, "url", "")))
        if is_login_url(current_url):
            return True
        try:
            marker = page.evaluate(
                """() => {
                    const path = window.location.pathname || '';
                    if (path.includes('/login')) return true;
                    const text = (document.body && document.body.innerText) || '';
                    return /sign in|log in|sso/i.test(text) && !path.includes('/superset/');
                }"""
            )
            return bool(marker)
        except Exception:
            return False

    def _force_refresh_sql_lab(self) -> None:
        page = self.auth_result.page
        if page is None:
            raise RuntimeError("No browser page available for SQL Lab refresh")
        self._emit_debug("session.force_refresh", url=self.sql_lab_url)
        # Hard navigation even if already on SQL Lab — clears stale SPA/query state.
        page.goto(self.sql_lab_url, wait_until="commit", timeout=PAGE_LOAD_TIMEOUT_MS)
        if self._page_looks_logged_out():
            raise RuntimeError("SQL Lab refresh landed on login (session expired)")
        navigate_sql_lab(page, self.sql_lab_url, timeout_ms=PAGE_LOAD_TIMEOUT_MS)
        wait_for_sql_lab_editor_ready(page)
        self._emit_debug("session.force_refresh.ready", url=sanitize_url(str(page.url)))

    def _relogin(self) -> None:
        self._emit_debug("session.relogin.start")
        try:
            self.auth_result.close()
        except Exception as exc:  # noqa: BLE001 - best-effort close before fresh login
            self._emit_debug("session.relogin.close_error", error=repr(exc))
        self.auth_result = self.auth.login_and_capture()
        self.session = self.auth.build_requests_session(self.auth_result.cookies)
        self._emit_debug(
            "session.relogin.done",
            url=sanitize_url(str(getattr(self.auth_result.page, "url", ""))),
        )

    def recover_session(self) -> None:
        """Force-refresh SQL Lab; if logged out, full re-login from scratch."""
        try:
            if self._page_looks_logged_out():
                self._relogin()
                return
            self._force_refresh_sql_lab()
        except Exception as refresh_error:
            self._emit_debug("session.force_refresh.failed", error=repr(refresh_error))
            # Refresh failed or landed on login → full re-auth.
            self._relogin()

    def recover_bot_block(self, attempt: int) -> None:
        """Bot block recovery: force-refresh first; later attempts invalidate + re-login."""
        backoff = self.bot_recovery_backoff_seconds
        delay = backoff[min(max(attempt, 1), len(backoff)) - 1]
        self._emit_debug("bot.recover.backoff", attempt=attempt, delay_s=delay)
        if delay > 0:
            time.sleep(delay)
        # First recovery: hard refresh SQL Lab only. Later: kill session and re-login.
        if attempt >= 2 or self._page_looks_logged_out():
            self._emit_debug("bot.recover.relogin", attempt=attempt)
            self._relogin()
            return
        try:
            self._emit_debug("bot.recover.force_refresh", attempt=attempt)
            self._force_refresh_sql_lab()
        except Exception as refresh_error:
            self._emit_debug("bot.recover.refresh_failed", error=repr(refresh_error))
            self._relogin()

    def __call__(self, sql: str) -> pd.DataFrame:
        session_recoveries = 0
        bot_recoveries = 0
        while True:
            try:
                return self._run_once(sql)
            except Exception as exc:
                if is_bot_block_error(exc):
                    if bot_recoveries >= self.max_bot_recoveries:
                        raise
                    bot_recoveries += 1
                    self._emit_debug(
                        "bot.recover",
                        attempt=bot_recoveries,
                        error=str(exc),
                    )
                    self.recover_bot_block(bot_recoveries)
                    continue
                if session_recoveries >= self.max_session_recoveries or not is_recoverable_session_error(exc):
                    raise
                session_recoveries += 1
                self._emit_debug(
                    "session.recover",
                    attempt=session_recoveries,
                    error=str(exc),
                )
                self.recover_session()

    def close(self) -> None:
        self.auth_result.close()

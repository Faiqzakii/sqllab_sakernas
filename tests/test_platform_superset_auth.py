from __future__ import annotations

from collections.abc import Sequence

import pytest

import app.engine.superset_auth as platform_auth
from app.engine.superset_auth import SupersetAuthBootstrap


class FakePage:
    def __init__(
        self,
        url: str,
        url_sequence_after_waits: Sequence[str] | None = None,
        update_url_on_goto: bool = False,
    ) -> None:
        self.url = url
        self.url_sequence_after_waits = list(url_sequence_after_waits or [])
        self.update_url_on_goto = update_url_on_goto
        self.goto_calls: list[tuple[str, str | None]] = []
        self.load_state_calls: list[str] = []
        self.wait_for_timeout_calls: list[int] = []
        self.wait_for_function_calls: list[tuple[str, object | None]] = []
        self.click_calls: list[str] = []
        self.fill_calls: list[tuple[str, str]] = []

    def goto(self, url: str, wait_until: str | None = None) -> None:
        self.goto_calls.append((url, wait_until))
        if self.update_url_on_goto:
            self.url = url

    def wait_for_load_state(self, state: str) -> None:
        self.load_state_calls.append(state)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_for_timeout_calls.append(milliseconds)
        if self.url_sequence_after_waits:
            self.url = self.url_sequence_after_waits.pop(0)

    def wait_for_function(self, script: str, arg: object | None = None) -> None:
        self.wait_for_function_calls.append((script, arg))

    def click(self, selector: str) -> None:
        self.click_calls.append(selector)

    def fill(self, selector: str, value: str) -> None:
        self.fill_calls.append((selector, value))

    def press(self, selector: str, key: str) -> None:
        return None

    def eval_on_selector(self, selector: str, script: str) -> None:
        return None


class FakeContext:
    def __init__(
        self,
        page: FakePage,
        cookies_sequence: Sequence[list[dict[str, object]]],
        extra_pages: Sequence[FakePage] | None = None,
    ) -> None:
        self.page = page
        self.cookies_sequence = list(cookies_sequence)
        self.extra_pages = list(extra_pages or [])
        self.new_page_calls = 0

    def new_page(self) -> FakePage:
        self.new_page_calls += 1
        if self.new_page_calls == 1:
            return self.page
        if self.extra_pages:
            return self.extra_pages.pop(0)
        return self.page

    def cookies(self) -> list[dict[str, object]]:
        if self.cookies_sequence:
            if len(self.cookies_sequence) == 1:
                return list(self.cookies_sequence[0])
            return list(self.cookies_sequence.pop(0))
        return []

    def close(self) -> None:
        return None


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context

    def new_context(self) -> FakeContext:
        return self.context

    def close(self) -> None:
        return None


class FakePlaywrightManager:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    def __enter__(self) -> object:
        chromium = type("Chromium", (), {"launch": lambda _self, headless=False: self.browser})()
        return type("Playwright", (), {"chromium": chromium})()

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_env_credential_login_opens_sql_lab_in_new_tab_after_welcome(monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = [{"name": "session", "value": "abc123", "domain": ".example.test"}]
    welcome_page = FakePage(
        url="https://example.test/login/",
        url_sequence_after_waits=["https://example.test/superset/welcome/"],
        update_url_on_goto=True,
    )
    sql_lab_page = FakePage(url="https://example.test/superset/sqllab/", update_url_on_goto=True)
    context = FakeContext(page=welcome_page, cookies_sequence=[cookies, cookies], extra_pages=[sql_lab_page])
    browser = FakeBrowser(context=context)
    monkeypatch.setattr(platform_auth, "sync_playwright", lambda: FakePlaywrightManager(browser))
    monkeypatch.setattr(
        platform_auth,
        "resolve_superset_credentials",
        lambda env_path=platform_auth.DEFAULT_ENV_PATH: ("alice", "secret"),
    )

    bootstrap = SupersetAuthBootstrap(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
    )

    result = bootstrap.login_and_capture()

    assert result.final_url == "https://example.test/superset/sqllab/"
    assert result.page is sql_lab_page
    assert context.new_page_calls == 2
    assert sql_lab_page.goto_calls == [("https://example.test/superset/sqllab/", "domcontentloaded")]

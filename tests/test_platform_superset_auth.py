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

    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.goto_calls.append((url, wait_until))
        if self.update_url_on_goto:
            self.url = url

    def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        self.load_state_calls.append(state)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_for_timeout_calls.append(milliseconds)
        if self.url_sequence_after_waits:
            self.url = self.url_sequence_after_waits.pop(0)

    def wait_for_function(
        self,
        script: str,
        arg: object | None = None,
        timeout: int | None = None,
    ) -> None:
        self.wait_for_function_calls.append((script, arg))
        # Simulate SPA landing on SQL Lab after commit navigation.
        if isinstance(arg, str) and arg == "/superset/sqllab/":
            self.url = "https://example.test/superset/sqllab/"

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


class FakeBrowserManager:
    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class FakeBrowserSession:
    def __init__(self, context: FakeContext, browser: FakeBrowser | None = None) -> None:
        self.manager = FakeBrowserManager()
        self.context = context
        self.browser = browser


def test_env_credential_login_opens_sql_lab_in_new_tab_after_welcome(monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = [{"name": "session", "value": "abc123", "domain": ".example.test"}]
    welcome_page = FakePage(
        url="https://example.test/login/",
        url_sequence_after_waits=["https://example.test/superset/welcome/"],
        update_url_on_goto=True,
    )
    sql_lab_page = FakePage(url="about:blank", update_url_on_goto=True)
    context = FakeContext(page=welcome_page, cookies_sequence=[cookies, cookies], extra_pages=[sql_lab_page])
    browser = FakeBrowser(context=context)
    launch_calls: list[dict[str, object]] = []

    def fake_launch(**kwargs: object) -> FakeBrowserSession:
        launch_calls.append(kwargs)
        return FakeBrowserSession(context=context, browser=None)

    monkeypatch.setattr(platform_auth, "launch_stealth_browser", fake_launch)
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
    assert result.browser_manager is not None
    assert context.new_page_calls == 2
    assert sql_lab_page.goto_calls == [("https://example.test/superset/sqllab/", "commit")]
    assert launch_calls == [{"headless": False}]


def test_live_host_requires_vpn_precheck_before_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = [{"name": "session", "value": "abc123", "domain": ".bps.go.id"}]
    page = FakePage(url="https://fasih-dashboard.bps.go.id/login/", update_url_on_goto=True)
    context = FakeContext(page=page, cookies_sequence=[cookies])
    browser = FakeBrowser(context=context)
    vpn_checks: list[str] = []
    launch_calls: list[dict[str, object]] = []

    def fake_launch(**kwargs: object) -> FakeBrowserSession:
        launch_calls.append(kwargs)
        return FakeBrowserSession(context=context, browser=None)

    monkeypatch.setattr(platform_auth, "launch_stealth_browser", fake_launch)
    monkeypatch.setattr(
        platform_auth,
        "resolve_superset_credentials",
        lambda env_path=platform_auth.DEFAULT_ENV_PATH: ("alice", "secret"),
    )
    monkeypatch.setattr(
        SupersetAuthBootstrap,
        "ensure_vpn_connected",
        lambda self: vpn_checks.append(self.base_url) or True,
    )
    monkeypatch.setattr(platform_auth, "click_sso_redirect_button", lambda page: "sso")
    monkeypatch.setattr(
        platform_auth,
        "submit_login_form",
        lambda page, username, password: {
            "username_selector": "u",
            "password_selector": "p",
            "submit_selector": "s",
        },
    )

    bootstrap = SupersetAuthBootstrap(
        base_url="https://fasih-dashboard.bps.go.id",
        sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
    )

    bootstrap.login_and_capture()

    assert vpn_checks == ["https://fasih-dashboard.bps.go.id"]


def test_live_host_stops_when_vpn_precheck_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    launch_calls: list[dict[str, object]] = []

    def fake_launch(**kwargs: object) -> FakeBrowserSession:
        launch_calls.append(kwargs)
        return FakeBrowserSession(
            context=FakeContext(FakePage(url="https://unused"), []),
            browser=None,
        )

    monkeypatch.setattr(platform_auth, "launch_stealth_browser", fake_launch)
    monkeypatch.setattr(
        platform_auth,
        "resolve_superset_credentials",
        lambda env_path=platform_auth.DEFAULT_ENV_PATH: ("alice", "secret"),
    )
    monkeypatch.setattr(SupersetAuthBootstrap, "ensure_vpn_connected", lambda self: False)

    bootstrap = SupersetAuthBootstrap(
        base_url="https://fasih-dashboard.bps.go.id",
        sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
    )

    with pytest.raises(RuntimeError, match="FortiClient VPN belum connect"):
        bootstrap.login_and_capture()

    assert launch_calls == []

def test_manual_login_clicks_sso_and_does_not_jump_to_sql_lab_from_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale profile cookies must not skip SSO or force /sqllab from /login."""
    stale_cookies = [{"name": "session", "value": "stale-session", "domain": ".example.test"}]
    page = FakePage(
        url="https://example.test/login/",
        # still on login for a while even though cookies exist (stale profile)
        url_sequence_after_waits=[
            "https://example.test/login/",
            "https://example.test/login/",
            "https://example.test/superset/welcome/",
        ],
        update_url_on_goto=True,
    )
    sql_lab_page = FakePage(url="about:blank", update_url_on_goto=True)
    context = FakeContext(
        page=page,
        cookies_sequence=[stale_cookies, stale_cookies, stale_cookies, stale_cookies],
        extra_pages=[sql_lab_page],
    )

    def fake_launch(**kwargs: object) -> FakeBrowserSession:
        return FakeBrowserSession(context=context, browser=None)

    monkeypatch.setattr(platform_auth, "launch_stealth_browser", fake_launch)
    monkeypatch.setattr(platform_auth, "AUTH_WAIT_POLL_INTERVAL_MS", 1)
    monkeypatch.setattr(platform_auth, "MANUAL_AUTH_WAIT_TIMEOUT_MS", 10)

    bootstrap = SupersetAuthBootstrap(
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        manual_login=True,
    )
    result = bootstrap.login_and_capture()

    assert platform_auth.SSO_REDIRECT_BUTTON_SELECTOR in page.click_calls
    # must not navigate the login page itself straight to SQL Lab
    assert all(not url.endswith("/superset/sqllab/") for url, _ in page.goto_calls)
    assert result.page is sql_lab_page
    assert sql_lab_page.goto_calls == [("https://example.test/superset/sqllab/", "commit")]
    assert context.new_page_calls == 2


def test_env_login_clicks_sso_before_form_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    cookies = [{"name": "session", "value": "abc123", "domain": ".example.test"}]
    welcome_page = FakePage(
        url="https://example.test/login/",
        url_sequence_after_waits=["https://example.test/superset/welcome/"],
        update_url_on_goto=True,
    )
    sql_lab_page = FakePage(url="about:blank", update_url_on_goto=True)
    context = FakeContext(page=welcome_page, cookies_sequence=[cookies, cookies], extra_pages=[sql_lab_page])

    def fake_launch(**kwargs: object) -> FakeBrowserSession:
        return FakeBrowserSession(context=context, browser=None)

    monkeypatch.setattr(platform_auth, "launch_stealth_browser", fake_launch)
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

    assert platform_auth.SSO_REDIRECT_BUTTON_SELECTOR in welcome_page.click_calls
    assert result.page is sql_lab_page


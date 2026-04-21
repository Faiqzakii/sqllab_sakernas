from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import time
from http.cookiejar import Cookie
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import requests
from playwright.sync_api import sync_playwright


PLAYWRIGHT_COOKIE_NAME = "name"
PLAYWRIGHT_COOKIE_VALUE = "value"
XSRF_COOKIE_NAME = "XSRF-TOKEN"
XSRF_HEADER_NAME = "X-XSRF-TOKEN"
AUTH_WAIT_TIMEOUT_MS = 30_000
MANUAL_AUTH_WAIT_TIMEOUT_MS = 120_000
AUTH_WAIT_POLL_INTERVAL_MS = 250
SESSION_COOKIE_NAMES = {
    "session",
    "sessionid",
    "session_id",
    "superset_session",
}
AUTH_COOKIE_NAMES = {
    "auth",
    "auth_token",
    "access_token",
    "refresh_token",
    "remember_token",
}
XSRF_COOKIE_NAMES = {
    "xsrf-token",
    "csrf-token",
    "csrftoken",
    "csrf_token",
    "_xsrf",
}
WELCOME_READY_XPATH = "/html/body/div[1]/div[2]/div/div[2]/div[2]/div/div[1]/div/ul/li[6]/div/a"
WELCOME_READY_TEXT = "All"
DEFAULT_ENV_PATH = Path(".env")
ENV_USERNAME_KEY = "SUPERSET_USERNAME"
ENV_PASSWORD_KEY = "SUPERSET_PASSWORD"
LOGIN_USERNAME_SELECTORS = (
    'input[name="username"]',
    'input[id="username"]',
    'input[type="email"]',
    'input[type="text"]',
)
LOGIN_PASSWORD_SELECTORS = (
    'input[name="password"]',
    'input[id="password"]',
    'input[type="password"]',
)
LOGIN_SUBMIT_SELECTORS = (
    'xpath=/html/body/div/div[2]/div/div/div/div/form/div[4]/input[2]',
    'button[type="submit"]',
    'input[type="submit"]',
    "button:has-text('Login')",
    "button:has-text('Sign in')",
    "button:has-text('Log in')",
)
SSO_REDIRECT_BUTTON_SELECTOR = "xpath=/html/body/div/div[2]/form/button"
FORTICLIENT_ADAPTER_NAME = "Fortinet SSL VPN Virtual Ethernet Adapter"
FORTICLIENT_EXE = r"C:\Program Files\Fortinet\FortiClient\FortiClient.exe"
VPN_CONNECT_TIMEOUT_SECONDS = 60
VPN_POLL_INTERVAL_SECONDS = 5


def playwright_cookies_to_header_dict(cookies: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(cookie[PLAYWRIGHT_COOKIE_NAME]): str(cookie[PLAYWRIGHT_COOKIE_VALUE])
        for cookie in cookies
        if PLAYWRIGHT_COOKIE_NAME in cookie and PLAYWRIGHT_COOKIE_VALUE in cookie
    }


def extract_xsrf_token(cookies: list[dict[str, Any]]) -> str | None:
    accepted_names = {XSRF_COOKIE_NAME.lower(), *XSRF_COOKIE_NAMES}
    for cookie in cookies:
        cookie_name = cookie.get(PLAYWRIGHT_COOKIE_NAME)
        if isinstance(cookie_name, str) and cookie_name.lower() in accepted_names:
            value = cookie.get(PLAYWRIGHT_COOKIE_VALUE)
            return None if value is None else str(value)
    return None


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def is_login_url(url: str) -> bool:
    return sanitize_url(url).rstrip("/").endswith("/login")


def is_welcome_url(url: str) -> bool:
    return sanitize_url(url).rstrip("/").endswith("/superset/welcome")


def cookie_matches_base_url(cookie: dict[str, Any], base_url: str) -> bool:
    parsed_base_url = urlparse(base_url)
    base_host = parsed_base_url.hostname
    cookie_domain = str(cookie.get("domain") or "")
    if not base_host:
        return True

    normalized_host = base_host.lower()
    normalized_domain = cookie_domain.lstrip(".").lower()
    return bool(normalized_domain) and (
        normalized_domain == normalized_host
        or normalized_host.endswith(f".{normalized_domain}")
    )


def normalized_origin(url: str) -> tuple[str, str, int]:
    parsed_url = urlparse(url)
    scheme = parsed_url.scheme.lower()
    hostname = parsed_url.hostname
    if scheme not in {"http", "https"} or hostname is None:
        raise ValueError("base_url and sql_lab_url must be absolute http(s) URLs")
    port = parsed_url.port or (443 if scheme == "https" else 80)
    return (scheme, hostname.lower(), port)


def validate_same_origin_url(url: str, base_url: str) -> None:
    candidate_origin = normalized_origin(url)
    expected_origin = normalized_origin(base_url)
    if candidate_origin != expected_origin:
        raise ValueError("sql_lab_url must use the same origin as base_url")


def is_likely_auth_cookie_name(name: str) -> bool:
    normalized_name = name.strip().lower()
    return (
        normalized_name == XSRF_COOKIE_NAME.lower()
        or normalized_name in SESSION_COOKIE_NAMES
        or normalized_name in AUTH_COOKIE_NAMES
        or normalized_name in XSRF_COOKIE_NAMES
    )


def is_authenticated_cookie(cookie: dict[str, Any], base_url: str) -> bool:
    name = cookie.get(PLAYWRIGHT_COOKIE_NAME)
    value = cookie.get(PLAYWRIGHT_COOKIE_VALUE)
    normalized_name = name.strip().lower() if isinstance(name, str) else ""
    return (
        isinstance(name, str)
        and value not in (None, "")
        and (
            normalized_name in SESSION_COOKIE_NAMES or normalized_name in AUTH_COOKIE_NAMES
        )
        and cookie_matches_base_url(cookie, base_url)
    )


def host_requires_vpn(base_url: str) -> bool:
    hostname = urlparse(base_url).hostname or ""
    return hostname.endswith("bps.go.id")


def is_completed_manual_login_cookie(cookie: dict[str, Any], base_url: str) -> bool:
    name = cookie.get(PLAYWRIGHT_COOKIE_NAME)
    value = cookie.get(PLAYWRIGHT_COOKIE_VALUE)
    return (
        isinstance(name, str)
        and value not in (None, "")
        and (name.strip().lower() in SESSION_COOKIE_NAMES or name.strip().lower() in AUTH_COOKIE_NAMES)
        and cookie_matches_base_url(cookie, base_url)
    )


def wait_for_auth_cookies(
    context: Any,
    page: Any,
    base_url: str,
    timeout_ms: int = AUTH_WAIT_TIMEOUT_MS,
    completion_predicate: Any | None = None,
) -> list[dict[str, Any]]:
    attempts = max(1, timeout_ms // AUTH_WAIT_POLL_INTERVAL_MS)
    latest_cookies = context.cookies()
    predicate = completion_predicate or is_authenticated_cookie
    if any(predicate(cookie, base_url) for cookie in latest_cookies):
        return latest_cookies

    for _ in range(attempts):
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(AUTH_WAIT_POLL_INTERVAL_MS)
        latest_cookies = context.cookies()
        if any(predicate(cookie, base_url) for cookie in latest_cookies):
            return latest_cookies

    return latest_cookies


def wait_for_welcome_ready_marker(page: Any) -> None:
    page.wait_for_function(
        "([xpath, expectedText]) => { const node = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; return node instanceof HTMLElement && node.textContent !== null && node.textContent.includes(expectedText); }",
        arg=[WELCOME_READY_XPATH, WELCOME_READY_TEXT],
    )


def load_dotenv_file(env_path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        normalized_value = value.strip()
        if len(normalized_value) >= 2 and normalized_value[0] == normalized_value[-1] and normalized_value[0] in {'"', "'"}:
            normalized_value = normalized_value[1:-1]
        values[normalized_key] = normalized_value
    return values


def resolve_superset_credentials(env_path: Path = DEFAULT_ENV_PATH) -> tuple[str, str] | None:
    file_values = load_dotenv_file(env_path)
    username = file_values.get(ENV_USERNAME_KEY) or os.environ.get(ENV_USERNAME_KEY)
    password = file_values.get(ENV_PASSWORD_KEY) or os.environ.get(ENV_PASSWORD_KEY)
    if not username or not password:
        return None
    return (username, password)


def fill_first_available_selector(page: Any, selectors: tuple[str, ...], value: str) -> str:
    last_error: Exception | None = None
    for selector in selectors:
        try:
            page.click(selector)
        except Exception:
            pass
        try:
            page.fill(selector, value)
            return selector
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise RuntimeError(f"Could not fill any login selector from {selectors}") from last_error
    raise RuntimeError(f"Could not fill any login selector from {selectors}")


def click_first_available_selector(page: Any, selectors: tuple[str, ...]) -> str:
    last_error: Exception | None = None
    for selector in selectors:
        try:
            page.click(selector)
            return selector
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise RuntimeError(f"Could not click any login selector from {selectors}") from last_error
    raise RuntimeError(f"Could not click any login selector from {selectors}")


def submit_via_password_enter(page: Any, password_selector: str) -> str:
    page.click(password_selector)
    page.press(password_selector, "Enter")
    return f"enter:{password_selector}"


def submit_via_form_script(page: Any, password_selector: str) -> str:
    page.eval_on_selector(
        password_selector,
        "(node) => { const form = node instanceof HTMLElement ? node.closest('form') : null; if (!(form instanceof HTMLFormElement)) { throw new Error('No parent form found'); } if (typeof form.requestSubmit === 'function') { form.requestSubmit(); return; } form.submit(); }",
    )
    return f"form:{password_selector}"


def submit_login_form(page: Any, username: str, password: str) -> dict[str, str]:
    username_selector = fill_first_available_selector(page, LOGIN_USERNAME_SELECTORS, username)
    password_selector = fill_first_available_selector(page, LOGIN_PASSWORD_SELECTORS, password)
    submit_attempts = (
        lambda: click_first_available_selector(page, LOGIN_SUBMIT_SELECTORS),
        lambda: submit_via_password_enter(page, password_selector),
        lambda: submit_via_form_script(page, password_selector),
    )
    last_error: Exception | None = None
    submit_selector: str | None = None
    for attempt in submit_attempts:
        try:
            submit_selector = attempt()
            break
        except Exception as exc:
            last_error = exc
    if submit_selector is None:
        raise RuntimeError("Could not submit login form with any strategy") from last_error
    return {
        "username_selector": username_selector,
        "password_selector": password_selector,
        "submit_selector": submit_selector,
    }


def click_sso_redirect_button(page: Any) -> str:
    page.click(SSO_REDIRECT_BUTTON_SELECTOR)
    return SSO_REDIRECT_BUTTON_SELECTOR


def playwright_cookie_to_requests_cookie(cookie: dict[str, Any]) -> Cookie | None:
    name = cookie.get(PLAYWRIGHT_COOKIE_NAME)
    value = cookie.get(PLAYWRIGHT_COOKIE_VALUE)
    if name is None or value is None:
        return None

    domain = str(cookie.get("domain") or "")
    path = str(cookie.get("path") or "/")
    rest: dict[str, Any] = {}
    if "httpOnly" in cookie:
        rest["HttpOnly"] = bool(cookie["httpOnly"])
    if cookie.get("sameSite") is not None:
        rest["SameSite"] = str(cookie["sameSite"])

    return requests.cookies.create_cookie(
        name=str(name),
        value=str(value),
        domain=domain,
        path=path,
        secure=bool(cookie.get("secure", False)),
        expires=cookie.get("expires"),
        rest=rest,
    )


class CleanupError(Exception):
    def __init__(self, errors: list[Exception]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(str(error) for error in self.errors))


def cleanup_resources(context: Any, browser: Any, playwright_manager: Any) -> None:
    cleanup_errors: list[Exception] = []
    if context is not None:
        try:
            context.close()
        except Exception as exc:
            cleanup_errors.append(exc)
    if browser is not None:
        try:
            browser.close()
        except Exception as exc:
            cleanup_errors.append(exc)
    if playwright_manager is not None:
        try:
            playwright_manager.__exit__(None, None, None)
        except Exception as exc:
            cleanup_errors.append(exc)
    if cleanup_errors:
        raise CleanupError(cleanup_errors)


@dataclass(frozen=True)
class AuthBootstrapResult:
    final_url: str
    cookies: list[dict[str, Any]]
    xsrf_token: str | None
    playwright_manager: Any | None = None
    playwright_instance: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None

    def close(self) -> None:
        cleanup_resources(self.context, self.browser, self.playwright_manager)


class SupersetAuthBootstrap:
    def __init__(self, base_url: str, sql_lab_url: str, manual_login: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.sql_lab_url = sql_lab_url
        self.manual_login = manual_login
        self.credentials = None if manual_login else resolve_superset_credentials()

    @staticmethod
    def _run_powershell(command: str) -> str:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()

    def is_vpn_connected(self) -> bool:
        status = self._run_powershell(
            f"(Get-NetAdapter -InterfaceDescription '{FORTICLIENT_ADAPTER_NAME}' -ErrorAction SilentlyContinue).Status"
        )
        return status.lower() == "up"

    def ensure_vpn_connected(self) -> bool:
        if not host_requires_vpn(self.base_url):
            return True

        if self.is_vpn_connected():
            return True

        if not os.path.exists(FORTICLIENT_EXE):
            return False

        try:
            subprocess.Popen([FORTICLIENT_EXE])
        except OSError:
            return False

        deadline = time.time() + VPN_CONNECT_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self.is_vpn_connected():
                return True
            time.sleep(VPN_POLL_INTERVAL_SECONDS)

        return False

    def login_and_capture(self) -> AuthBootstrapResult:
        validate_same_origin_url(self.sql_lab_url, self.base_url)
        if not self.ensure_vpn_connected():
            raise RuntimeError(
                "FortiClient VPN is required before connecting to Superset. Connect VPN first, then retry."
            )
        playwright_manager = sync_playwright()
        playwright = playwright_manager.__enter__()
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        try:
            page = context.new_page()
            use_login_page = self.manual_login or self.credentials is not None
            initial_url = urljoin(f"{self.base_url}/", "login/") if use_login_page else self.base_url
            page.goto(initial_url, wait_until="domcontentloaded")
            timeout_ms = MANUAL_AUTH_WAIT_TIMEOUT_MS if self.manual_login else AUTH_WAIT_TIMEOUT_MS

            if self.credentials is not None:
                username, password = self.credentials
                click_sso_redirect_button(page)
                submit_login_form(page, username, password)

            if not self.manual_login and self.credentials is None:
                page.goto(self.sql_lab_url, wait_until="domcontentloaded")
            cookies = wait_for_auth_cookies(
                context=context,
                page=page,
                base_url=self.base_url,
                timeout_ms=timeout_ms,
                completion_predicate=(
                    is_completed_manual_login_cookie if self.manual_login else is_authenticated_cookie
                ),
            )
            final_url = page.url
            reached_welcome = is_welcome_url(page.url)
            auth_cookie_predicate = (
                is_completed_manual_login_cookie if self.manual_login else is_authenticated_cookie
            )
            if any(auth_cookie_predicate(cookie, self.base_url) for cookie in cookies):
                attempts = max(1, timeout_ms // AUTH_WAIT_POLL_INTERVAL_MS)
                for _ in range(attempts):
                    if is_welcome_url(page.url):
                        reached_welcome = True
                        break
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(AUTH_WAIT_POLL_INTERVAL_MS)
                    cookies = context.cookies()
                    if is_welcome_url(page.url):
                        reached_welcome = True
                    if self.manual_login and is_login_url(page.url) and any(
                        is_completed_manual_login_cookie(cookie, self.base_url)
                        for cookie in cookies
                    ):
                        page.goto(self.sql_lab_url, wait_until="domcontentloaded")
                        final_url = page.url
                        if is_welcome_url(page.url):
                            reached_welcome = True
                            break
                final_url = page.url
            if reached_welcome and is_welcome_url(final_url):
                welcome_url = sanitize_url(final_url)
                page.wait_for_load_state("load")
                if sanitize_url(page.url) == welcome_url:
                    wait_for_welcome_ready_marker(page)
                    sql_lab_page = context.new_page()
                    sql_lab_page.goto(self.sql_lab_url, wait_until="domcontentloaded")
                    page = sql_lab_page
                    final_url = page.url
                    cookies = context.cookies()
            if self.manual_login and not any(
                is_completed_manual_login_cookie(cookie, self.base_url) for cookie in cookies
            ):
                raise RuntimeError("Manual login was not completed before timeout")
            if self.manual_login and is_login_url(final_url):
                raise RuntimeError(
                    "Manual login cookies were captured, but the app remained on /login/"
                )
            if self.manual_login and not reached_welcome:
                raise RuntimeError(
                    "Manual login cookies were captured, but the app never reached /superset/welcome/"
                )
        except Exception as exc:
            try:
                cleanup_resources(context, browser, playwright_manager)
            except CleanupError as cleanup_error:
                exc.__context__ = cleanup_error
            raise

        return AuthBootstrapResult(
            final_url=sanitize_url(final_url),
            cookies=cookies,
            xsrf_token=extract_xsrf_token(cookies),
            playwright_manager=playwright_manager,
            playwright_instance=playwright,
            browser=browser,
            context=context,
            page=page,
        )

    def build_requests_session(self, cookies: list[dict[str, Any]]) -> requests.Session:
        validate_same_origin_url(self.sql_lab_url, self.base_url)
        session = requests.Session()
        filtered_cookies = [
            cookie
            for cookie in cookies
            if cookie_matches_base_url(cookie, self.base_url)
            and is_likely_auth_cookie_name(str(cookie.get(PLAYWRIGHT_COOKIE_NAME) or ""))
        ]
        for cookie in filtered_cookies:
            requests_cookie = playwright_cookie_to_requests_cookie(cookie)
            if requests_cookie is not None:
                session.cookies.set_cookie(requests_cookie)
        xsrf_token = extract_xsrf_token(filtered_cookies)
        if xsrf_token:
            session.headers[XSRF_HEADER_NAME] = xsrf_token
        return session

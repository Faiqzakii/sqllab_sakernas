# Camoufox Stealth Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stock Playwright Chromium/Edge with Camoufox stealth browser for Superset auth bootstrap and SQL Lab UI runner.

**Architecture:** One thin `browser_factory.launch_stealth_browser()` owns Camoufox launch defaults (headed, persistent profile, humanize, block_webrtc, enable_cache). `SupersetAuthBootstrap` and `SupersetUiRunner` consume that factory. SQL Lab interaction and result capture stay unchanged.

**Tech Stack:** Python 3.11+, Camoufox (Playwright-compatible Firefox), pytest, existing FastAPI platform code.

## Global Constraints

- Full cutover: no Playwright dual-engine flag.
- Default headed: `headless=False`.
- Persistent profile at `.camoufox-profile/` unless `SUPERSET_BROWSER_PROFILE_DIR` overrides.
- Stealth defaults: `humanize=True`, `block_webrtc=True`, `enable_cache=True`, `os="windows"`, `locale="id-ID"`.
- Net rename: `playwright_manager` → `browser_manager`; drop `playwright_instance`.
- Cookie helper renames: `playwright_cookies_to_header_dict` → `browser_cookies_to_header_dict`, `playwright_cookie_to_requests_cookie` → `browser_cookie_to_requests_cookie`.
- Preserve SQL Lab contract: editor fill → Run → `/execute` → `resultsKey` → `/results`.
- TDD: failing test first for every behavior change.
- Commits: conventional (`feat:`, `fix:`, `test:`, `docs:`, `chore:`). Do not push without user confirmation.
- Language: Indonesian for communication, English for code/comments.

## File Map

| File | Role |
|------|------|
| Create: `src/app/engine/browser_factory.py` | Camoufox launch + profile resolution + session type |
| Create: `tests/test_browser_factory.py` | Factory unit tests |
| Modify: `src/app/engine/superset_auth.py` | Auth uses factory; rename manager/helpers |
| Modify: `src/app/engine/superset_ui_runner.py` | UI self-launch uses factory; drop Chromium |
| Modify: `tests/test_platform_superset_auth.py` | Patch factory; assert stealth launch |
| Modify: `tests/test_ui_runner.py` | Assert Camoufox error text / factory self-launch if needed |
| Modify: `pyproject.toml` | `playwright` → `camoufox` |
| Modify: `.gitignore` | ignore `.camoufox-profile/` |
| Modify: `README.md`, `agent.md` | setup notes `camoufox fetch` |

---

### Task 1: Browser factory + tests

**Files:**
- Create: `src/app/engine/browser_factory.py`
- Create: `tests/test_browser_factory.py`
- Modify: `.gitignore`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Camoufox package (`from camoufox.sync_api import Camoufox`)
- Produces:
  - `DEFAULT_PROFILE_DIR: Path`
  - `PROFILE_ENV_KEY: str = "SUPERSET_BROWSER_PROFILE_DIR"`
  - `resolve_profile_dir(explicit: str | Path | None = None) -> Path`
  - `@dataclass class BrowserSession: manager: Any; context: Any; browser: Any | None = None`
  - `launch_stealth_browser(*, profile_dir: str | Path | None = None, headless: bool = False) -> BrowserSession`
  - `close_browser_session(session: BrowserSession) -> None`

- [ ] **Step 1: Write failing factory tests**

Create `tests/test_browser_factory.py`:

```python
from __future__ import annotations

from pathlib import Path

import app.engine.browser_factory as browser_factory


class FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeCamoufoxManager:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.entered = False
        self.exited = False
        self.context = FakeContext()

    def __enter__(self) -> FakeContext:
        self.entered = True
        return self.context

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exited = True


def test_resolve_profile_dir_defaults_to_repo_dot_camoufox(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(browser_factory.PROFILE_ENV_KEY, raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = browser_factory.resolve_profile_dir()

    assert resolved == (tmp_path / ".camoufox-profile").resolve()


def test_resolve_profile_dir_honors_env(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom-profile"
    monkeypatch.setenv(browser_factory.PROFILE_ENV_KEY, str(custom))

    resolved = browser_factory.resolve_profile_dir()

    assert resolved == custom.resolve()


def test_launch_stealth_browser_passes_approved_defaults(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_camoufox(**kwargs: object) -> FakeCamoufoxManager:
        manager = FakeCamoufoxManager(**kwargs)
        calls.append(kwargs)
        return manager

    monkeypatch.setattr(browser_factory, "Camoufox", fake_camoufox)
    profile = tmp_path / "profile"

    session = browser_factory.launch_stealth_browser(profile_dir=profile)

    assert profile.exists()
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["headless"] is False
    assert kwargs["os"] == "windows"
    assert kwargs["locale"] == "id-ID"
    assert kwargs["humanize"] is True
    assert kwargs["block_webrtc"] is True
    assert kwargs["enable_cache"] is True
    assert kwargs["persistent_context"] is True
    assert Path(str(kwargs["user_data_dir"])) == profile.resolve()
    assert session.context is not None
    assert session.browser is None
    assert session.manager is not None


def test_close_browser_session_closes_context_and_manager(tmp_path: Path, monkeypatch) -> None:
    def fake_camoufox(**kwargs: object) -> FakeCamoufoxManager:
        return FakeCamoufoxManager(**kwargs)

    monkeypatch.setattr(browser_factory, "Camoufox", fake_camoufox)
    session = browser_factory.launch_stealth_browser(profile_dir=tmp_path / "p")

    browser_factory.close_browser_session(session)

    assert session.context.closed is True
    assert session.manager.exited is True
```

- [ ] **Step 2: Run tests — expect fail**

Run:

```bat
pytest tests/test_browser_factory.py -v
```

Expected: FAIL import error `No module named 'app.engine.browser_factory'` or similar.

- [ ] **Step 3: Implement factory**

Create `src/app/engine/browser_factory.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from camoufox.sync_api import Camoufox

DEFAULT_PROFILE_DIR = Path(".camoufox-profile")
PROFILE_ENV_KEY = "SUPERSET_BROWSER_PROFILE_DIR"


@dataclass
class BrowserSession:
    manager: Any
    context: Any
    browser: Any | None = None


def resolve_profile_dir(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(PROFILE_ENV_KEY)
    if env_value:
        return Path(env_value).expanduser().resolve()
    return DEFAULT_PROFILE_DIR.expanduser().resolve()


def launch_stealth_browser(
    *,
    profile_dir: str | Path | None = None,
    headless: bool = False,
) -> BrowserSession:
    resolved_profile = resolve_profile_dir(profile_dir)
    resolved_profile.mkdir(parents=True, exist_ok=True)

    manager = Camoufox(
        headless=headless,
        os="windows",
        locale="id-ID",
        humanize=True,
        block_webrtc=True,
        enable_cache=True,
        persistent_context=True,
        user_data_dir=str(resolved_profile),
    )
    context = manager.__enter__()
    return BrowserSession(manager=manager, context=context, browser=None)


def close_browser_session(session: BrowserSession) -> None:
    errors: list[Exception] = []
    if session.context is not None:
        try:
            session.context.close()
        except Exception as exc:  # noqa: BLE001 - aggregate cleanup
            errors.append(exc)
    if session.browser is not None:
        try:
            session.browser.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    if session.manager is not None:
        try:
            session.manager.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    if errors:
        raise RuntimeError(f"Browser session cleanup failed: {errors}")
```

Update `pyproject.toml` dependencies:

```toml
dependencies = [
  "fastapi>=0.115.0",
  "uvicorn[standard]>=0.30.0",
  "sqlmodel>=0.0.22",
  "pydantic-settings>=2.4.0",
  "pandas>=2.2.0",
  "duckdb>=1.1.0",
  "httpx>=0.27.0",
  "python-multipart>=0.0.9",
  "jinja2>=3.1.0",
  "camoufox>=0.4.11",
  "requests>=2.32.0",
]
```

Append to `.gitignore`:

```gitignore
.camoufox-profile/
```

- [ ] **Step 4: Install dependency and re-run tests**

```bat
pip install -e .
pytest tests/test_browser_factory.py -v
```

Expected: PASS all 4 tests.

If `camoufox` version floor fails on install, pin the newest version that installs cleanly and update both `pyproject.toml` and this plan note.

- [ ] **Step 5: Commit**

```bat
git add src/app/engine/browser_factory.py tests/test_browser_factory.py pyproject.toml .gitignore
git commit -m "feat: add Camoufox stealth browser factory"
```

---

### Task 2: Migrate auth bootstrap to Camoufox

**Files:**
- Modify: `src/app/engine/superset_auth.py`
- Modify: `tests/test_platform_superset_auth.py`

**Interfaces:**
- Consumes: `launch_stealth_browser`, `BrowserSession` from `app.engine.browser_factory`
- Produces:
  - `AuthBootstrapResult.browser_manager` (replaces `playwright_manager`)
  - no `playwright_instance`
  - `browser_cookies_to_header_dict`, `browser_cookie_to_requests_cookie`
  - `cleanup_resources(context, browser, browser_manager)`

- [ ] **Step 1: Update auth tests to expect factory**

In `tests/test_platform_superset_auth.py`:

1. Replace FakePlaywright stack that expects `sync_playwright` + `chromium.launch(channel="msedge")` with a fake `launch_stealth_browser` returning a session that exposes the existing FakeBrowser/FakeContext/FakePage graph.
2. Change assertion:

```python
# OLD
assert playwright_manager.launch_calls == [{"channel": "msedge", "headless": False}]

# NEW
assert launch_calls == [{"profile_dir": None, "headless": False}]
# or whatever kwargs the auth code passes through
```

3. VPN tests that assert `sync_playwright` not called should assert `launch_stealth_browser` not called instead.

Sketch for happy-path monkeypatch:

```python
def test_login_and_capture_opens_sql_lab_in_new_tab_after_welcome(monkeypatch):
    # ... build fake welcome page + sql lab page + context + browser as today ...
    launch_calls: list[dict[str, object]] = []

    class FakeSession:
        def __init__(self) -> None:
            self.manager = object()
            self.context = context
            self.browser = None

    def fake_launch(**kwargs: object) -> FakeSession:
        launch_calls.append(kwargs)
        return FakeSession()

    monkeypatch.setattr(platform_auth, "launch_stealth_browser", fake_launch)
    # remove monkeypatch of sync_playwright
    ...
    result = bootstrap.login_and_capture()
    assert result.page is sql_lab_page
    assert result.browser_manager is not None
    assert not hasattr(result, "playwright_manager") or not hasattr(type(result), "playwright_manager")
    assert launch_calls  # factory used
```

Also update any imports/usages of renamed cookie helpers if tests call them directly.

- [ ] **Step 2: Run auth tests — expect fail**

```bat
pytest tests/test_platform_superset_auth.py -v
```

Expected: FAIL because production still uses Playwright / old field names.

- [ ] **Step 3: Implement auth migration**

In `src/app/engine/superset_auth.py`:

1. Replace import:

```python
# remove
from playwright.sync_api import sync_playwright

# add
from app.engine.browser_factory import launch_stealth_browser
```

2. Rename helpers:

```python
BROWSER_COOKIE_NAME = "name"
BROWSER_COOKIE_VALUE = "value"
# update all PLAYWRIGHT_COOKIE_* usages to BROWSER_COOKIE_*
```

```python
def browser_cookies_to_header_dict(...): ...
def browser_cookie_to_requests_cookie(...): ...
```

3. Rename cleanup + result:

```python
def cleanup_resources(context: Any, browser: Any, browser_manager: Any) -> None:
    ...
    if browser_manager is not None:
        browser_manager.__exit__(None, None, None)


@dataclass(frozen=True)
class AuthBootstrapResult:
    final_url: str
    cookies: list[dict[str, Any]]
    xsrf_token: str | None
    browser_manager: Any | None = None
    browser: Any | None = None
    context: Any | None = None
    page: Any | None = None

    def close(self) -> None:
        cleanup_resources(self.context, self.browser, self.browser_manager)
```

4. Replace launch body in `login_and_capture`:

```python
session = launch_stealth_browser(headless=False)
browser_manager = session.manager
browser = session.browser
context = session.context
try:
    page = context.new_page()
    # ... existing login / welcome / new-tab flow unchanged ...
except Exception as exc:
    try:
        cleanup_resources(context, browser, browser_manager)
    except CleanupError as cleanup_error:
        exc.__context__ = cleanup_error
    raise

return AuthBootstrapResult(
    final_url=sanitize_url(final_url),
    cookies=cookies,
    xsrf_token=extract_xsrf_token(cookies),
    browser_manager=browser_manager,
    browser=browser,
    context=context,
    page=page,
)
```

5. Grep and fix any internal references to old names in the same file.

- [ ] **Step 4: Run auth tests — expect pass**

```bat
pytest tests/test_platform_superset_auth.py -v
```

Expected: PASS.

Also run:

```bat
pytest tests/test_superset_executor.py tests/test_superset_engine_smoke.py -v
```

Expected: PASS (or fix only rename fallout if any).

- [ ] **Step 5: Commit**

```bat
git add src/app/engine/superset_auth.py tests/test_platform_superset_auth.py
git commit -m "feat: migrate Superset auth bootstrap to Camoufox"
```

---

### Task 3: Migrate SQL Lab UI runner self-launch

**Files:**
- Modify: `src/app/engine/superset_ui_runner.py`
- Modify: `tests/test_ui_runner.py` (only if assertions/import paths require it)

**Interfaces:**
- Consumes: `launch_stealth_browser`, `close_browser_session` from `app.engine.browser_factory`
- Produces: same `SupersetUiRunner.run_query(sql) -> QueryResult` behavior

- [ ] **Step 1: Add/adjust failing coverage for self-launch path**

Existing tests inject `page=` so they keep passing. Add one focused test that proves factory is used when page/context absent:

```python
def test_ui_runner_self_launch_uses_browser_factory(monkeypatch) -> None:
    launch_calls: list[dict[str, object]] = []

    class FakePage:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}
            self.goto_calls: list[tuple[str, str]] = []

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

    class FakeContext:
        def __init__(self) -> None:
            self.pages: list[FakePage] = []
            self.added_cookies: list[list[dict[str, object]]] = []

        def new_page(self) -> FakePage:
            page = FakePage()
            self.pages.append(page)
            return page

        def add_cookies(self, cookies: list[dict[str, object]]) -> None:
            self.added_cookies.append(cookies)

        def close(self) -> None:
            return None

    class FakeSession:
        def __init__(self) -> None:
            self.manager = object()
            self.context = FakeContext()
            self.browser = None

    def fake_launch(**kwargs: object) -> FakeSession:
        launch_calls.append(kwargs)
        return FakeSession()

    monkeypatch.setattr(ui_runner_module, "launch_stealth_browser", fake_launch)
    monkeypatch.setattr(ui_runner_module, "close_browser_session", lambda session: None)

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
```

Also change any remaining string assertions from Playwright → Camoufox if present.

- [ ] **Step 2: Run UI runner tests — expect new test fail / old pass**

```bat
pytest tests/test_ui_runner.py -v
```

Expected: new self-launch test fails until implementation; existing page-injected tests still pass.

- [ ] **Step 3: Implement UI runner migration**

In `src/app/engine/superset_ui_runner.py`:

1. Remove:

```python
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
```

2. Add:

```python
from app.engine.browser_factory import close_browser_session, launch_stealth_browser
```

3. In `run_query`, replace managed launch:

```python
if self.page is None and self.context is None:
    # no optional import gate on sync_playwright
    pass

managed_session = None
try:
    if self.page is not None:
        page = self.page
    elif self.context is not None:
        page = self.context.new_page()
    else:
        managed_session = launch_stealth_browser(headless=self.headless)
        managed_context = managed_session.context
        if self.auth_cookies:
            managed_context.add_cookies(self.auth_cookies)
        page = managed_context.new_page()
        self.context = managed_context
        self.browser = managed_session.browser
    # ... rest of query flow unchanged ...
finally:
    # existing listener cleanup
    if managed_session is not None:
        close_browser_session(managed_session)
```

4. Replace error text if any remains:

```python
raise RuntimeError("Camoufox is required for UI fallback execution")
```

only where a missing-runtime condition still needs messaging (prefer letting import of factory fail naturally if package missing).

5. Delete obsolete type aliases that exist only for Playwright manager if unused (`PlaywrightContextManager`, etc.) — keep `BrowserPage`/`BrowserContext` Protocol-style aliases if they still help typing.

- [ ] **Step 4: Run UI runner + auth tests**

```bat
pytest tests/test_ui_runner.py tests/test_platform_superset_auth.py tests/test_superset_executor.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bat
git add src/app/engine/superset_ui_runner.py tests/test_ui_runner.py
git commit -m "feat: run SQL Lab UI fallback on Camoufox stealth browser"
```

---

### Task 4: Residual cleanup, docs, verification

**Files:**
- Modify: `README.md`
- Modify: `agent.md`
- Possibly touch any residual references found by grep

**Interfaces:**
- Consumes: completed factory/auth/UI migration
- Produces: clean repo with no stock Playwright runtime dependency in app code

- [ ] **Step 1: Grep residual Playwright runtime usage**

```bat
rg -n "playwright|sync_playwright|msedge|playwright_manager|playwright_instance|Playwright is required" src tests scripts manual_*.py pyproject.toml README.md agent.md
```

Expected leftovers only if historical docs intentionally mention migration history. Production runtime paths must not import Playwright.

Fix any remaining production references.

- [ ] **Step 2: Update docs**

In `README.md`, near setup/run instructions, add:

```markdown
## Browser runtime (Camoufox)

SQL Lab auth/UI uses Camoufox (stealth Firefox), not stock Playwright Chromium.

```bat
pip install -e .
camoufox fetch
```

Persistent profile directory: `.camoufox-profile/` (gitignored).
Override with env `SUPERSET_BROWSER_PROFILE_DIR`.
```

In `agent.md` section about UI runner / architecture:

- Replace “Uses Playwright…” with “Uses Camoufox stealth browser via `browser_factory`…”
- Note profile path and `camoufox fetch` prerequisite.

- [ ] **Step 3: Full focused verification**

```bat
pytest tests/test_browser_factory.py tests/test_platform_superset_auth.py tests/test_ui_runner.py tests/test_superset_executor.py tests/test_superset_engine_smoke.py -v
```

Expected: all PASS.

Optional operator smoke (not required for code completion):

```bat
camoufox fetch
python -c "from app.engine.browser_factory import launch_stealth_browser, close_browser_session; s=launch_stealth_browser(); print('ok', s.context); close_browser_session(s)"
```

- [ ] **Step 4: Commit docs/cleanup**

```bat
git add README.md agent.md
git commit -m "docs: document Camoufox setup for SQL Lab runner"
```

---

## Spec Coverage Check

| Spec requirement | Task |
|------------------|------|
| Camoufox-only cutover | 1–4 |
| Headed default | 1, 2, 3 |
| Persistent profile `.camoufox-profile/` | 1 |
| Env override `SUPERSET_BROWSER_PROFILE_DIR` | 1 |
| humanize + block_webrtc + enable_cache | 1 |
| `browser_manager` rename | 2 |
| Drop `playwright_instance` | 2 |
| Cookie helper renames | 2 |
| Auth welcome → SQL Lab new tab preserved | 2 |
| UI runner self-launch via factory | 3 |
| Result capture semantics unchanged | 3 (no logic rewrite) |
| pyproject dependency swap | 1 |
| gitignore profile | 1 |
| docs `camoufox fetch` | 4 |
| residual Playwright grep | 4 |
| focused unit tests | 1–4 |

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-23-camoufox-stealth-migration.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — execute tasks in this session with checkpoints

Which approach?

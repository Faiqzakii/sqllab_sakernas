# Camoufox Stealth Migration Design

## Goal

Migrate the Superset SQL Lab Platform browser runtime from stock Playwright Chromium/Edge to Camoufox with stealth defaults, while preserving the existing SQL Lab execution contract.

This is a runtime migration, not a product redesign. Login flow, SQL editor interaction, `/execute` → `resultsKey` → `/results` capture, batching, and backend API fallback stay the same.

## Problem

Current browser path:

- `src/app/engine/superset_auth.py` launches Playwright Chromium via `channel="msedge"`.
- `src/app/engine/superset_ui_runner.py` can also self-launch Playwright Chromium when no page/context is injected.
- Live Superset access is sensitive to automation fingerprints and shared browser state.

Desired outcome:

- one Camoufox-backed stealth browser for auth and SQL Lab UI runner
- headed by default
- persistent browser profile between runs
- no dual Playwright/Camoufox engine switch

## Approved Decisions

| Decision | Choice |
|----------|--------|
| Engine | Camoufox only (full cutover) |
| Default visibility | Headed (`headless=False`) |
| Session profile | Persistent profile |
| Profile path | `.camoufox-profile/` at repo root |
| Stealth defaults | Aggressive: `humanize=True`, `block_webrtc=True`, `enable_cache=True` |
| Result naming | Net rename: `playwright_manager` → `browser_manager` |
| Architecture | Thin browser factory shared by auth + UI runner |

## Non-Goals

- Dual-engine flag (`playwright` | `camoufox`)
- Proxy / GeoIP configuration in this migration
- Changes to SQL Lab row-cap, batch fan-out, or result correlation logic
- Changes to dashboard product UI
- Live Superset integration tests in CI

## Architecture

```text
browser_factory.launch_stealth_browser()
  └─ Camoufox(
       headless=False,
       os="windows",
       locale="id-ID",
       humanize=True,
       block_webrtc=True,
       enable_cache=True,
       persistent_context=True,
       user_data_dir=<profile dir>,
     )
  └─ BrowserSession(manager, context, browser=None|wrapper)

SupersetAuthBootstrap.login_and_capture()
  └─ uses factory
  └─ existing VPN precheck + login + welcome + SQL Lab new-tab flow
  └─ AuthBootstrapResult(
       final_url, cookies, xsrf_token,
       browser_manager, browser, context, page
     )

SupersetUiRunner.run_query()
  └─ prefers injected page/context from auth
  └─ if forced self-launch, uses the same factory
  └─ existing editor fill / Run click / response capture
```

Camoufox is a Playwright-compatible Firefox wrapper. Existing page APIs used by this project (`goto`, `click`, `fill`, `evaluate`, `on("response")`, `cookies`, `new_page`) remain valid.

## Component Design

### 1. `src/app/engine/browser_factory.py` (new)

Responsibilities:

- resolve profile directory
- launch Camoufox with approved stealth defaults
- return a small session object usable by auth and UI runner
- expose cleanup that closes context and exits the Camoufox manager

Defaults:

```python
DEFAULT_PROFILE_DIR = Path(".camoufox-profile")
PROFILE_ENV_KEY = "SUPERSET_BROWSER_PROFILE_DIR"

DEFAULT_STEALTH = {
    "headless": False,
    "os": "windows",
    "locale": "id-ID",
    "humanize": True,
    "block_webrtc": True,
    "enable_cache": True,
    "persistent_context": True,
}
```

Profile resolution order:

1. explicit argument if provided
2. `SUPERSET_BROWSER_PROFILE_DIR` env var
3. `DEFAULT_PROFILE_DIR`

`launch_stealth_browser()` must create the profile directory if missing.

Because `persistent_context=True` makes Camoufox yield a **context**, the session model is:

```python
@dataclass
class BrowserSession:
    manager: Any          # Camoufox context manager instance
    context: Any          # persistent browser context
    browser: Any | None   # None for persistent context path
```

Callers that currently expect `browser` may receive `None`. All production callers that matter already pass `context` and/or `page`.

### 2. `src/app/engine/superset_auth.py`

Changes:

- remove direct `from playwright.sync_api import sync_playwright`
- launch via `launch_stealth_browser()`
- rename runtime fields:
  - `playwright_manager` → `browser_manager`
  - drop `playwright_instance` (unused by callers; manager is enough for cleanup)
- rename cookie helper names that hardcode Playwright:
  - `playwright_cookies_to_header_dict` → `browser_cookies_to_header_dict`
  - `playwright_cookie_to_requests_cookie` → `browser_cookie_to_requests_cookie`
  - cookie key constants may stay (`name` / `value`) because Camoufox/Playwright cookie dict shape is the same
- preserve behavior:
  - VPN precheck for `*.bps.go.id`
  - credential login / manual login
  - wait for auth cookies
  - wait for `/superset/welcome`
  - open SQL Lab in a **new tab** after welcome ready marker

`AuthBootstrapResult.close()` continues to clean context then manager. With persistent context, cleanup order is:

1. close context if open
2. exit `browser_manager`
3. browser close is a no-op when `browser is None`

### 3. `src/app/engine/superset_ui_runner.py`

Changes:

- remove Chromium self-launch via `sync_playwright().chromium.launch(...)`
- if `page` is provided: use it
- elif `context` is provided: `context.new_page()`
- else: launch through `browser_factory.launch_stealth_browser()`, create page from that context, and clean it up in `finally`
- if cookies are provided on self-launch path, call `context.add_cookies(...)` after launch
- error text becomes Camoufox-oriented, e.g. `Camoufox is required for UI fallback execution`
- all SQL Lab interaction logic stays unchanged:
  - editor fill / Ace focus
  - Run click
  - response listener for `/api/v1/sqllab/execute/` and `/api/v1/sqllab/results/`
  - reject stale results / mismatched `resultsKey` / mismatched query id
  - visible table fallback

### 4. Consumers that should keep working without logic changes

These already take `auth_result.browser/context/page` and must continue to work after rename:

- `src/app/engine/superset_executor.py`
- `src/app/engine/superset_query_runner.py`
- manual QA scripts under repo root

If any consumer references `playwright_manager` directly, update to `browser_manager`.

### 5. Dependencies and setup

`pyproject.toml`:

- remove `playwright>=1.55.0`
- add `camoufox>=0.4.11` (or newest available that installs cleanly on the project Python; pin exact version discovered during implementation)

Setup documentation (`README.md`, `agent.md`):

```bat
pip install -e .
camoufox fetch
```

Runtime failure if browser binary missing must surface a clear message telling the operator to run `camoufox fetch`.

### 6. Ignore rules

`.gitignore` gains:

```gitignore
.camoufox-profile/
```

The persistent profile may contain cookies/session state and must not be committed.

## Data Flow

### Auth bootstrap

1. Validate `sql_lab_url` same origin as `base_url`.
2. Ensure VPN when host requires it.
3. Launch Camoufox stealth session with persistent profile.
4. Open login or base URL.
5. Perform credential SSO/form submit or wait for manual login.
6. Wait for auth cookies, then welcome readiness.
7. Open SQL Lab in a new page/tab.
8. Return cookies + live context/page for reuse.

### SQL Lab query

1. Reuse auth page when provided by sequential executor.
2. Or open a fresh page from shared auth context for folder/batch runner.
3. Fill SQL editor and verify readback.
4. Click Run.
5. Accept network `/results` payload correlated to active execute metadata.
6. Fall back to visible table only when network result is absent and table signature changed.
7. Return `QueryResult(source="ui", ...)`.

## Error Handling

| Case | Behavior |
|------|----------|
| Camoufox package missing | Import/runtime error with install guidance |
| Camoufox browser not fetched | Clear error: run `camoufox fetch` |
| Profile directory locked by another process | RuntimeError including profile path |
| VPN missing on live host | Existing FortiClient error, before browser launch |
| Login timeout / still on `/login/` | Existing auth errors unchanged |
| Editor SQL mismatch / pending execute / missing results | Existing UI runner errors unchanged |
| Cleanup partial failure | Aggregate via existing `CleanupError` pattern |

## Testing Strategy

### Unit / regression (required)

1. **Browser factory**
   - resolves default profile path `.camoufox-profile`
   - honors `SUPERSET_BROWSER_PROFILE_DIR`
   - passes stealth kwargs: headed, humanize, block_webrtc, enable_cache, persistent_context, os, locale

2. **Auth bootstrap**
   - monkeypatch factory instead of Playwright Chromium manager
   - still opens SQL Lab in new tab after welcome
   - still blocks launch when VPN precheck fails
   - asserts no `channel="msedge"` launch path remains

3. **UI runner**
   - existing response-correlation tests remain green
   - self-launch path uses factory when page/context absent
   - no direct Playwright Chromium import/launch remains

### Explicit non-tests

- No live Superset CI job required for this migration.
- Manual smoke against `fasih-dashboard.bps.go.id` is operator-side after VPN login.

## Migration Checklist

1. Add `browser_factory.py`.
2. Point auth bootstrap at factory; rename manager/cookie helpers.
3. Point UI runner self-launch at factory; delete Chromium launch.
4. Update dependency and ignore rules.
5. Update tests and docs.
6. Grep for residual `playwright` / `msedge` / `sync_playwright` usage under `src/`, `tests/`, scripts.
7. Run focused pytest suite for auth + UI runner + executor.

## Acceptance Criteria

- Project no longer depends on stock Playwright package directly.
- Auth and SQL Lab UI runner launch Camoufox only.
- Default browser is headed.
- Persistent profile lives at `.camoufox-profile/` unless overridden by env.
- Stealth defaults include humanize, block_webrtc, and enable_cache.
- `AuthBootstrapResult` exposes `browser_manager` (not `playwright_manager`).
- Existing SQL Lab result-capture semantics remain unchanged.
- Focused unit tests pass.
- Profile directory is gitignored.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Camoufox Firefox quirks vs previous Edge path | Keep page API usage generic; preserve existing wait/readback guards |
| Persistent profile retains stale session | Auth still validates cookies/welcome; operator can delete `.camoufox-profile/` |
| Higher memory from `enable_cache=True` | Accepted by design; revisit only if runs become unstable |
| Persistent context returns context not browser | Session object documents `browser=None`; callers use context/page |

## Rollout

1. Implement and unit-test offline.
2. Operator runs `camoufox fetch`.
3. Manual smoke: login + one SQL Lab query on live host with VPN.
4. If profile corruption suspected, delete `.camoufox-profile/` and retry.

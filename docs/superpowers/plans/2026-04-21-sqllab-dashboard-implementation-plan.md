# SQLLAB Dashboard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a viewer/admin dashboard for the Superset SQL Lab Platform with read-only Overview and Findings for all users, plus admin-only Configuration and Run Control after login.

**Architecture:** Extend the existing FastAPI app with session-based role gating, dashboard-oriented read APIs, and HTML UI routes/templates for Overview, Findings, Configuration, and Run Control. Reuse the existing domain entities (`JobDefinition`, `Run`, `RunStepExecution`, `DatasetSnapshot`, `FindingRuleHit`, `IdentityReviewState`) instead of inventing new product abstractions.

**Tech Stack:** FastAPI, SQLModel, SQLite, existing Superset SQL Lab execution services, server-rendered HTML or lightweight FastAPI UI surface, pytest.

---

## File Structure

### Existing files likely to modify

- `src/app/main.py` — app wiring
- `src/app/web.py` — existing HTML UI routes; likely to be replaced or expanded into dashboard routes
- `src/app/api/resources.py` — configuration and execute endpoints
- `src/app/api/runs.py` — run creation and detail surface
- `src/app/api/findings.py` — findings list and review-state mutation
- `src/app/services/jobs.py` — execution result shape and freshness metadata extraction
- `src/app/services/runs.py` — run detail helpers
- `src/app/services/findings.py` — findings aggregation helpers
- `src/app/models.py` — add auth/session model only if persistence is required; avoid expanding domain unless necessary
- `tests/test_app_boot.py`
- `tests/test_runs_api.py`
- `tests/test_findings_service.py`
- `tests/test_resources_api.py`

### New files to create

- `src/app/auth.py` — simple session auth and role helpers
- `src/app/api/dashboard.py` — read-only dashboard summary endpoints for overview/freshness if separated from HTML routes
- `src/app/services/dashboard.py` — overview/freshness aggregation service
- `src/app/templates/overview.html` — overview page template if moving beyond inline HTML
- `src/app/templates/findings.html` — findings page template
- `src/app/templates/login.html` — login page template
- `src/app/templates/configuration.html` — admin configuration page template
- `src/app/templates/run_control.html` — admin run control page template
- `tests/test_auth.py` — login/session/role tests
- `tests/test_dashboard_service.py` — overview aggregation tests
- `tests/test_dashboard_web.py` — UI route access tests

If the codebase remains inline-HTML only, collapse templates into route handlers rather than introducing a larger templating system. Prefer the smallest change that produces a clear dashboard structure.

---

## Chunk 1: Dashboard data model and read surfaces

### Task 1: Add dashboard aggregation service

**Files:**
- Create: `src/app/services/dashboard.py`
- Test: `tests/test_dashboard_service.py`

- [ ] **Step 1: Write the failing test for overview summary aggregation**

```python
def test_build_overview_summary_returns_latest_dataset_and_findings_summary():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_service.py -v`
Expected: FAIL because `app.services.dashboard` does not exist yet.

- [ ] **Step 3: Implement minimal overview aggregation service**

Include helpers that return:
- latest dataset row count
- last successful update timestamp
- last anomaly/findings query timestamp
- findings summary by severity/review state

Use persisted timestamp fields on `Run` and `DatasetSnapshot` as the primary source of truth. Only use filesystem mtimes as a legacy fallback if a row predates the schema upgrade.

Current code reality to preserve during implementation:
- `Run` stores `created_at`, `started_at`, `completed_at`, and `failed_at`.
- `DatasetSnapshot` stores `created_at` and `duckdb_artifact_path` in addition to `run_id`, `row_count`, and `artifact_path`.
- `data/dataset.duckdb` is written directly from in-memory merged rows in `app.services.jobs._write_snapshot_duckdb_artifact(...)`.
- DuckDB is **not** rebuilt by reading `artifacts/snapshots/{run_id}/dataset.json`.

So the dashboard implementation should retrieve last-run freshness from persisted lifecycle timestamps first, then fall back to file metadata only for legacy rows that lack those values.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_dashboard_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/services/dashboard.py tests/test_dashboard_service.py
git commit -m "feat: add dashboard overview aggregation service"
```

### Task 2: Add read-only dashboard API surface

**Files:**
- Create: `src/app/api/dashboard.py`
- Modify: `src/app/main.py`
- Test: `tests/test_dashboard_web.py`

- [ ] **Step 1: Write failing tests for overview summary endpoint**

```python
def test_overview_summary_endpoint_returns_expected_fields():
    ...
```

- [ ] **Step 2: Run the failing tests**

Run: `pytest tests/test_dashboard_web.py -v`
Expected: FAIL because endpoint/router does not exist.

- [ ] **Step 3: Implement dashboard summary route**

Return only the approved Overview fields.

- [ ] **Step 4: Wire router into app**

Modify `src/app/main.py` to include the dashboard router.

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_dashboard_web.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/app/api/dashboard.py src/app/main.py tests/test_dashboard_web.py
git commit -m "feat: add read-only dashboard summary api"
```

---

## Chunk 2: Viewer/admin auth foundation

### Task 3: Introduce simple session-based auth helpers

**Files:**
- Create: `src/app/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests for viewer default and admin login**

```python
def test_request_defaults_to_viewer_role():
    ...

def test_successful_login_elevates_session_to_admin():
    ...
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL because auth helpers do not exist.

- [ ] **Step 3: Implement minimal session auth**

Include:
- default viewer role
- admin login verification using a simple configured credential source
- role-check helpers/dependencies for admin-only pages and actions

Keep it simple; do not design full RBAC.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/auth.py tests/test_auth.py
git commit -m "feat: add viewer admin session auth"
```

### Task 4: Protect admin-only routes

**Files:**
- Modify: `src/app/api/resources.py`
- Modify: `src/app/api/runs.py`
- Modify: `src/app/api/findings.py`
- Test: `tests/test_resources_api.py`
- Test: `tests/test_runs_api.py`

- [ ] **Step 1: Write failing tests for unauthorized admin actions**

```python
def test_viewer_cannot_execute_job_definition():
    ...

def test_viewer_cannot_create_run():
    ...
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_resources_api.py tests/test_runs_api.py -v`
Expected: FAIL because routes are currently unprotected.

- [ ] **Step 3: Add admin guards to mutating routes**

Protect:
- create/edit config routes
- execute job route
- run creation route
- optional review-state update route if admin-only in final implementation

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_resources_api.py tests/test_runs_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/api/resources.py src/app/api/runs.py src/app/api/findings.py tests/test_resources_api.py tests/test_runs_api.py
git commit -m "feat: protect admin mutation routes"
```

---

## Chunk 3: Viewer pages

### Task 5: Build Overview page

**Files:**
- Modify: `src/app/web.py` or create a dedicated dashboard web module
- Create: `src/app/templates/overview.html` (if templating is introduced)
- Test: `tests/test_dashboard_web.py`

- [ ] **Step 1: Write failing UI test for overview page**

```python
def test_overview_page_shows_only_approved_summary_sections():
    ...
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_dashboard_web.py::test_overview_page_shows_only_approved_summary_sections -v`
Expected: FAIL because page does not exist in final form.

- [ ] **Step 3: Implement Overview page**

Render:
- total rows latest dataset
- last successful update timestamp
- last anomaly/findings query timestamp
- findings summary

Do not render run control or config actions here.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_dashboard_web.py::test_overview_page_shows_only_approved_summary_sections -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/web.py src/app/templates/overview.html tests/test_dashboard_web.py
git commit -m "feat: add viewer overview page"
```

### Task 6: Build Findings page

**Files:**
- Modify: `src/app/web.py`
- Create: `src/app/templates/findings.html`
- Test: `tests/test_dashboard_web.py`

- [ ] **Step 1: Write failing test for findings page**

```python
def test_findings_page_lists_identity_findings_for_viewer():
    ...
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_dashboard_web.py::test_findings_page_lists_identity_findings_for_viewer -v`
Expected: FAIL

- [ ] **Step 3: Implement Findings page**

Render:
- filters/search controls
- list/table of findings
- finding detail section

Use existing findings service and endpoint shape.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_dashboard_web.py::test_findings_page_lists_identity_findings_for_viewer -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/web.py src/app/templates/findings.html tests/test_dashboard_web.py
git commit -m "feat: add findings explorer page"
```

### Task 7: Build Login page and admin navigation change

**Files:**
- Modify: `src/app/web.py`
- Create: `src/app/templates/login.html`
- Test: `tests/test_dashboard_web.py`

- [ ] **Step 1: Write failing test for login route and admin navigation**

```python
def test_login_promotes_viewer_to_admin_session():
    ...
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_dashboard_web.py::test_login_promotes_viewer_to_admin_session -v`
Expected: FAIL

- [ ] **Step 3: Implement login UI and session elevation**

After login success:
- render admin nav items
- keep viewer pages visible

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_dashboard_web.py::test_login_promotes_viewer_to_admin_session -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/web.py src/app/templates/login.html tests/test_dashboard_web.py
git commit -m "feat: add admin login flow"
```

---

## Chunk 4: Admin configuration surface

### Task 8: Build Configuration page

**Files:**
- Modify: `src/app/web.py`
- Create: `src/app/templates/configuration.html`
- Modify: `src/app/api/resources.py` if update endpoints are needed
- Test: `tests/test_dashboard_web.py`
- Test: `tests/test_resources_api.py`

- [ ] **Step 1: Write failing tests for admin-only configuration page**

```python
def test_viewer_cannot_open_configuration_page():
    ...

def test_admin_configuration_page_shows_jobdefinition_fields():
    ...
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_dashboard_web.py tests/test_resources_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Configuration page**

Show:
- `name`
- `execution_mode`
- `sql_template`
- `params_schema_json`
- `merge_key_columns_json`
- `identity_columns_json`

Provide simple edit/save flow. Avoid overbuilding a generic config management system.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_dashboard_web.py tests/test_resources_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/app/web.py src/app/templates/configuration.html src/app/api/resources.py tests/test_dashboard_web.py tests/test_resources_api.py
git commit -m "feat: add admin configuration page"
```

---

## Chunk 5: Admin run control

### Task 9: Build Run Control page for extraction and anomaly/findings

**Files:**
- Modify: `src/app/web.py`
- Create: `src/app/templates/run_control.html`
- Modify: `src/app/api/runs.py`
- Modify: `src/app/services/runs.py`
- Test: `tests/test_dashboard_web.py`
- Test: `tests/test_runs_api.py`

- [ ] **Step 1: Write failing tests for admin-only Run Control page**

```python
def test_admin_run_control_page_shows_run_actions_and_monitor():
    ...
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_dashboard_web.py tests/test_runs_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement Run Control page**

Render:
- Run extraction button
- Run anomaly/findings button
- latest execution status blocks
- run step list
- error/debug summary
- artifact/output references

Admin only.

- [ ] **Step 4: Add backend support for anomaly/findings run orchestration shape if missing**

If the codebase lacks a dedicated anomaly-run endpoint, add the thinnest possible orchestration layer consistent with existing services.

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_dashboard_web.py tests/test_runs_api.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/app/web.py src/app/templates/run_control.html src/app/api/runs.py src/app/services/runs.py tests/test_dashboard_web.py tests/test_runs_api.py
git commit -m "feat: add admin run control page"
```

---

## Chunk 6: Final verification and polish

### Task 10: End-to-end verification

**Files:**
- Modify as needed: any touched files
- Test: `tests/test_app_boot.py`
- Test: `tests/test_dashboard_web.py`
- Test: `tests/test_auth.py`
- Test: `tests/test_dashboard_service.py`

- [ ] **Step 1: Run the focused test suite**

Run: `pytest tests/test_auth.py tests/test_dashboard_service.py tests/test_dashboard_web.py tests/test_resources_api.py tests/test_runs_api.py tests/test_app_boot.py -v`
Expected: PASS

- [ ] **Step 2: Run lsp/diagnostics or app boot verification**

Run the app and verify routes load.

- [ ] **Step 3: Manual QA**

Verify:
- viewer sees Overview + Findings only
- viewer cannot access Configuration or Run Control
- admin login reveals Configuration + Run Control
- Overview contains only approved summary groups
- Run Control contains both extraction and anomaly/findings actions

- [ ] **Step 4: Commit final polish**

```bash
git add src/app tests
git commit -m "feat: finalize viewer admin dashboard"
```

---

## Notes for implementers

- Do not create a separate Dataset Snapshots page.
- Do not place run monitor information in Overview.
- Keep viewer mode useful without login.
- Keep auth simple; do not build unnecessary RBAC.
- Prefer existing models and services over introducing new parallel abstractions.

Plan complete and saved to `docs/superpowers/plans/2026-04-21-sqllab-dashboard-implementation-plan.md`. Ready to execute?

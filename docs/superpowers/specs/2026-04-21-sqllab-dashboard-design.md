# SQLLAB Viewer/Admin Dashboard Design

## Goal

Design a dashboard for the Superset SQL Lab Platform that supports two access levels:

- **Viewer** by default
- **Admin** after successful login

The dashboard must reflect the codebase as it exists today: a local FastAPI application centered on `JobDefinition`, `Run`, `RunStepExecution`, `DatasetSnapshot`, `RuleDefinition`, `FindingRuleHit`, and `IdentityReviewState`.

## Product Summary

The product is an internal data-operations dashboard for:

- monitoring the freshness of the latest pooled dataset,
- reviewing anomaly/finding results,
- configuring SQL Lab extraction behavior,
- and running extraction or anomaly/findings queries.

The dashboard is intentionally split into two product modes:

- **Observe**: read-only visibility for all users
- **Control**: admin-only configuration and execution surfaces

This separation follows the current codebase and avoids exposing mutation controls in the general overview.

## Approved Information Architecture

### Viewer-visible pages

1. **Overview**
2. **Findings**
3. **Login**

### Admin-only pages after login

4. **Configuration**
5. **Run Control**

## Access Model

### Viewer

Viewer is the default role for all users before login.

Viewer can:

- open the dashboard,
- see the latest data freshness and anomaly summary,
- search and inspect findings.

Viewer cannot:

- edit any configuration,
- trigger extraction jobs,
- trigger anomaly/findings runs,
- access execution history or run-monitor details,
- access admin-only pages.

### Admin

Admin is a role elevation that occurs after successful login.

Admin can:

- access all viewer surfaces,
- access Configuration,
- access Run Control,
- update configuration for SQL Lab extraction,
- trigger extraction job runs,
- trigger anomaly/findings runs,
- inspect execution detail for those runs.

### Authentication scope

The current codebase does **not** include application-level auth, sessions, or role models. Existing auth code is only for logging into Superset so SQL Lab queries can execute. Therefore:

- viewer/admin auth must be introduced as a new application feature,
- initial implementation should use a simple internal login and session role,
- no multi-user administration is required in the initial version.

## Page Specifications

## 1. Overview

### Purpose

Provide a simple read-only summary of the latest data and anomaly state.

### Content

Overview must show only:

- **Total rows from the latest dataset**
- **Timestamp of last successful update**
- **Timestamp of the last anomaly/findings query run**
- **Summary of findings / anomaly detection**

### Explicit exclusions

Overview must **not** include:

- detailed run monitor data,
- execution history,
- configuration forms,
- any trigger buttons,
- dataset snapshots as a separate feature area.

### Data mapping

Overview should derive from:

- `DatasetSnapshot.row_count`
- latest dataset freshness derived from persisted `Run` and `DatasetSnapshot` timestamp fields
- latest anomaly/findings execution freshness derived from persisted `Run` lifecycle timestamps
- aggregated findings from `FindingRuleHit` and `IdentityReviewState`

### Current implementation note: freshness and persistence

The current codebase now persists explicit timing fields for execution lifecycle tracking:

- `Run.created_at`
- `Run.started_at`
- `Run.completed_at`
- `Run.failed_at`
- `DatasetSnapshot.created_at`

Today, the persisted models only store:

- `Run.id`, `job_definition_id`, `status`, `created_at`, `started_at`, `completed_at`, `failed_at`
- `RunStepExecution.id`, `run_id`, `step_type`, `status`
- `DatasetSnapshot.id`, `run_id`, `row_count`, `artifact_path`, `duckdb_artifact_path`, `created_at`

Therefore, Overview freshness should now come from persisted records first:

- last successful dataset refresh: latest successful extraction `Run.completed_at`, optionally joined to `DatasetSnapshot.created_at`
- last anomaly/findings query run: latest successful anomaly/findings `Run.completed_at`
- latest dataset row count: latest `DatasetSnapshot.row_count`

Filesystem mtimes remain a fallback for legacy data, but they are no longer the primary source of truth for last-run timestamps.

### UX tone

Overview answers only:

- What is the latest dataset size?
- When was data last refreshed?
- When were anomaly/findings last queried?
- What is the current anomaly summary?

It is a health surface, not an operator console.

## 2. Findings

### Purpose

Provide a searchable, filterable workspace for anomaly results.

### Viewer behavior

Viewer can:

- search findings,
- filter by identity key, severity, rule, and review state,
- inspect finding details.

### Admin behavior

Admin sees the same page and may optionally gain review-state mutation controls during implementation if desired.

### Content

- search/filter bar,
- results list/table,
- detail panel/drawer,
- finding severity and rule references,
- identity payload summary.

### Data mapping

Primary source:

- `GET /identity-findings`

Optional admin action:

- `POST /identity-findings/{identity_key}/status`

## 3. Login

### Purpose

Elevate the user from viewer to admin.

### Behavior

- all users start in viewer mode,
- login is a lightweight entry point,
- after successful login, the session is marked admin,
- admin-only navigation becomes visible.

### Non-goals for initial version

- multi-user management,
- tenant separation,
- advanced role hierarchies,
- external identity-provider complexity.

## 4. Configuration (admin only)

### Purpose

Expose the actual job configuration surfaces that already exist in the codebase.

### Content

Configuration should manage:

- `JobDefinition.name`
- `JobDefinition.execution_mode`
- `JobDefinition.sql_template`
- `JobDefinition.params_schema_json`
- `JobDefinition.merge_key_columns_json`
- `JobDefinition.identity_columns_json`

It should also expose SQL Lab-related fields currently carried inside params data, such as:

- `base_url`
- `sql_lab_url`
- `source_data_path`
- `batching_strategy`

### UX rules

- show friendly form controls for known fields,
- keep a raw JSON editor only for advanced parameters,
- do not invent configuration concepts not present in the codebase,
- keep the panel focused on SQL Lab extraction configuration.

### Explicit exclusions

- Dataset Snapshots must not become a separate page or management area.

## 5. Run Control (admin only)

### Purpose

Provide the single admin workspace for running and monitoring execution.

### Required actions

Run Control must allow admin users to:

- **Run extraction job**
- **Run anomaly/findings**

### Required monitor behavior

Run Control must also include monitoring for those actions:

- latest status,
- current/last execution state,
- step-level status,
- error/debug summary,
- output/artifact references.

### Important rule

Run monitor is **not** a standalone viewer feature. It is integrated into Run Control and visible only to admin users.

### Data mapping

Run Control should be built from:

- `POST /job-definitions/{job_definition_id}/execute`
- `POST /runs`
- `Run`
- `RunStepExecution`
- execution outputs from services/artifacts

### Current implementation note: DuckDB write path

The platform currently writes outputs in this order inside `app.services.jobs.execute_job_definition(...)`:

1. batch/query results are accumulated in memory as pandas DataFrames
2. those DataFrames are merged into `merged_dataframe`
3. `merged_dataframe.to_dict(orient="records")` becomes `merged_rows`
4. `merged_rows` are written to the JSON artifact at `artifacts/snapshots/{run_id}/dataset.json`
5. the **same in-memory `merged_rows`** are written directly to `data/dataset.duckdb`

Important: DuckDB is **not** populated by re-reading the JSON artifact. Both JSON and DuckDB outputs are sibling write targets produced from the same in-memory merged dataset.

## Domain Mapping to Current Codebase

### Existing models

- `ConnectionProfile`
- `JobDefinition`
- `Run`
- `RunStepExecution`
- `DatasetSnapshot`
- `RuleDefinition`
- `RuleExecution` (model exists, but not yet fully surfaced)
- `FindingRuleHit`
- `IdentityReviewState`

### Existing backend surfaces

- `POST /job-definitions`
- `GET /job-definitions`
- `POST /job-definitions/{job_definition_id}/execute`
- `POST /rule-definitions`
- `GET /rule-definitions`
- `POST /snapshots`
- `GET /snapshots`
- `POST /runs`
- `GET /identity-findings`
- `POST /identity-findings/{identity_key}/status`

### Existing UI surfaces

- `/ui/runs`
- `/ui/identity-findings`

These are minimal HTML pages and should be treated as scaffolding, not the final dashboard architecture.

## API Contract Status

The current FastAPI API contract is **not yet sufficient** for the approved dashboard architecture.

### What already exists

Existing endpoints:

- `POST /job-definitions`
- `GET /job-definitions`
- `POST /job-definitions/{job_definition_id}/execute`
- `POST /rule-definitions`
- `GET /rule-definitions`
- `POST /snapshots`
- `GET /snapshots`
- `POST /runs`
- `GET /identity-findings`
- `POST /identity-findings/{identity_key}/status`

These are useful building blocks, but they do not yet form a clean dashboard contract.

### What is missing

The backend still needs explicit contracts for:

1. **Overview summary**
   - one endpoint returning latest dataset row count, last successful update timestamp, last anomaly/findings query timestamp, and findings summary

2. **Auth/session**
   - login
   - logout
   - current session / role lookup
   - role enforcement for admin-only actions

3. **Run Control monitoring**
   - latest extraction run summary
   - latest anomaly/findings run summary
   - run list/history
   - run detail by id
   - step detail and status
   - output/artifact references

4. **Anomaly/findings execution**
   - a dedicated trigger contract for running anomaly/findings
   - execution status/result metadata for that process

5. **Configuration updates**
   - explicit update endpoints for dashboard-managed `JobDefinition` fields and SQL Lab config fields

### Design implication

The docs should treat API contract repair as a prerequisite step. Frontend work, especially a React frontend, should not start until the backend contracts above are explicitly defined and tested.

## Final API Contract

This is the recommended concrete FastAPI contract for the approved dashboard.

### Versioning

Use a versioned API prefix:

- `/api/v1/...`

### Auth / Session

#### `POST /api/v1/auth/login`

Purpose: elevate a viewer session to admin.

Request:

```json
{
  "username": "admin",
  "password": "secret"
}
```

Response `200`:

```json
{
  "data": {
    "role": "admin",
    "authenticated": true
  }
}
```

Response `401`:

```json
{
  "error": {
    "code": "invalid_credentials",
    "message": "Invalid username or password"
  }
}
```

#### `POST /api/v1/auth/logout`

Purpose: clear admin session and return to viewer mode.

Response `200`:

```json
{
  "data": {
    "role": "viewer",
    "authenticated": false
  }
}
```

#### `GET /api/v1/auth/session`

Purpose: return the current session role for frontend gating.

Response `200`:

```json
{
  "data": {
    "role": "viewer",
    "authenticated": false
  }
}
```

or

```json
{
  "data": {
    "role": "admin",
    "authenticated": true
  }
}
```

### Overview

#### `GET /api/v1/dashboard/overview`

Access: viewer and admin

Response `200`:

```json
{
  "data": {
    "latest_dataset": {
      "row_count": 1400000,
      "last_successful_update_at": "2026-04-21T08:42:00Z"
    },
    "anomaly_query": {
      "last_run_at": "2026-04-21T09:10:00Z"
    },
    "findings_summary": {
      "total": 183,
      "by_severity": {
        "critical": 27,
        "warn": 91,
        "info": 65
      },
      "by_review_state": {
        "open": 120,
        "reviewed": 40,
        "closed": 23
      }
    }
  }
}
```

### Findings

#### `GET /api/v1/findings`

Access: viewer and admin

Query parameters:

- `identity_key`
- `severity`
- `rule_id`
- `review_state`
- `page`
- `per_page`

Response `200`:

```json
{
  "data": [
    {
      "identity_key": "id-001",
      "highest_severity": "critical",
      "rule_ids": [1, 2],
      "review_state": "open",
      "identity_payload": {
        "identity_key": "id-001",
        "household_number": "12"
      }
    }
  ],
  "meta": {
    "total": 1,
    "page": 1,
    "per_page": 20,
    "total_pages": 1
  }
}
```

#### `GET /api/v1/findings/{identity_key}`

Access: viewer and admin

Response `200`:

```json
{
  "data": {
    "identity_key": "id-001",
    "highest_severity": "critical",
    "rule_ids": [1, 2],
    "review_state": "open",
    "identity_payload": {
      "identity_key": "id-001",
      "household_number": "12"
    }
  }
}
```

#### `PATCH /api/v1/findings/{identity_key}/review-state`

Access: admin only

Request:

```json
{
  "review_state": "reviewed"
}
```

Response `200`:

```json
{
  "data": {
    "identity_key": "id-001",
    "review_state": "reviewed"
  }
}
```

### Configuration

#### `GET /api/v1/config/job-definition`

Access: admin only

Response `200`:

```json
{
  "data": {
    "id": 1,
    "name": "settlement_monitoring_project",
    "execution_mode": "superset_sql",
    "sql_template": "select ...",
    "params_schema_json": {
      "base_url": "https://superset.local",
      "sql_lab_url": "https://superset.local/sqllab/",
      "source_data_path": "data/source.json",
      "batching_strategy": {
        "param": "level_2_code",
        "values": ["01", "02", "03"]
      }
    },
    "merge_key_columns_json": ["identity_key"],
    "identity_columns_json": ["identity_key"]
  }
}
```

#### `PATCH /api/v1/config/job-definition`

Access: admin only

Request:

```json
{
  "name": "settlement_monitoring_project",
  "execution_mode": "superset_sql",
  "sql_template": "select ...",
  "params_schema_json": {
    "base_url": "https://superset.local",
    "sql_lab_url": "https://superset.local/sqllab/",
    "source_data_path": "data/source.json",
    "batching_strategy": {
      "param": "level_2_code",
      "values": ["01", "02", "03"]
    }
  },
  "merge_key_columns_json": ["identity_key"],
  "identity_columns_json": ["identity_key"]
}
```

Response `200`:

```json
{
  "data": {
    "id": 1,
    "updated": true
  }
}
```

### Run Control

#### `POST /api/v1/run-control/extraction`

Access: admin only

Request:

```json
{
  "job_definition_id": 1,
  "debug": false
}
```

Response `202`:

```json
{
  "data": {
    "run_type": "extraction",
    "run_id": 55,
    "status": "pending"
  }
}
```

#### `POST /api/v1/run-control/anomaly`

Access: admin only

Request:

```json
{
  "dataset_snapshot_id": 55
}
```

Response `202`:

```json
{
  "data": {
    "run_type": "anomaly",
    "run_id": 77,
    "status": "pending"
  }
}
```

#### `GET /api/v1/run-control/latest`

Access: admin only

Response `200`:

```json
{
  "data": {
    "extraction": {
      "run_id": 55,
      "status": "completed",
      "completed_at": "2026-04-21T08:42:00Z"
    },
    "anomaly": {
      "run_id": 77,
      "status": "completed",
      "completed_at": "2026-04-21T09:10:00Z"
    }
  }
}
```

#### `GET /api/v1/run-control/runs`

Access: admin only

Query parameters:

- `run_type=extraction|anomaly`
- `page`
- `per_page`

Response `200`:

```json
{
  "data": [
    {
      "run_id": 55,
      "run_type": "extraction",
      "status": "completed",
      "created_at": "2026-04-21T08:40:00Z",
      "completed_at": "2026-04-21T08:42:00Z"
    }
  ],
  "meta": {
    "total": 1,
    "page": 1,
    "per_page": 20,
    "total_pages": 1
  }
}
```

#### `GET /api/v1/run-control/runs/{run_id}`

Access: admin only

Response `200`:

```json
{
  "data": {
    "run_id": 55,
    "run_type": "extraction",
    "status": "completed",
    "job_definition_id": 1,
    "steps": [
      {
        "id": 1,
        "step_type": "superset_sql:01",
        "status": "completed"
      },
      {
        "id": 2,
        "step_type": "snapshot_merge",
        "status": "completed"
      }
    ],
    "outputs": {
      "row_count": 1400000,
      "artifact_path": "artifacts/snapshots/55/dataset.json",
      "duckdb_artifact_path": "data/dataset.duckdb",
      "batch_debug_path": "artifacts/snapshots/55/batches.json"
    }
  }
}
```

### Access Rules Summary

#### Viewer

Allowed:

- `GET /api/v1/auth/session`
- `GET /api/v1/dashboard/overview`
- `GET /api/v1/findings`
- `GET /api/v1/findings/{identity_key}`

#### Admin

Allowed:

- all viewer endpoints
- `POST /api/v1/auth/logout`
- `PATCH /api/v1/findings/{identity_key}/review-state`
- `GET /api/v1/config/job-definition`
- `PATCH /api/v1/config/job-definition`
- all `/api/v1/run-control/*` endpoints

### Status Code Rules

Recommended status codes:

- `200` for successful reads and updates
- `202` for async run triggers
- `401` for unauthenticated requests
- `403` for forbidden viewer-to-admin access
- `404` for missing resources
- `422` for validation errors
- `500` for unexpected internal failures
- `502` / `503` for upstream Superset failures when applicable

## Product Constraints

1. The dashboard must reflect the current platform shape rather than a generic analytics product.
2. The dashboard must preserve a clean separation between observation and control.
3. Admin-only features must be hidden and protected.
4. Viewer mode must remain useful even without login.
5. The initial auth model should stay simple.

## Out of Scope

- Dataset Snapshots as a dedicated user-facing feature page
- Full enterprise RBAC
- Historical snapshot warehouse UX
- Multi-user admin management
- Complex audit/compliance systems in v1
- Rebuilding the Superset auth machinery itself

## Recommended Delivery Order

1. Overview
2. Findings
3. Login + viewer/admin session
4. Configuration
5. Run Control

## Acceptance Criteria

The design is satisfied when:

- viewer can open Overview and Findings without admin access,
- admin can log in and see Configuration + Run Control,
- Overview contains only the approved four summary groups,
- Findings supports search and detail inspection,
- Configuration maps directly to the existing job/config fields,
- Run Control supports both extraction and anomaly/findings runs,
- run monitoring is admin-only and lives inside Run Control.

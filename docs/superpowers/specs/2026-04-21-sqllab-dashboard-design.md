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
- latest dataset artifact / DuckDB freshness metadata
- latest anomaly/findings execution freshness metadata
- aggregated findings from `FindingRuleHit` and `IdentityReviewState`

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

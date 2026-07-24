# Superset SQL Lab Platform - Agent Handoff

This file is the current engineering handoff for the `superset_sqllab_platform` project.

It is intentionally practical, not aspirational.

Use this file first before modifying code.

---

## 1. Project purpose

This project is a local web application for:

- running batched Superset SQL Lab queries,
- merging results into one dataset,
- storing the latest queryable dataset locally,
- preparing for anomaly checks and operational findings,
- and eventually exposing those findings in a web UI.

At the moment, the project is in an advanced scaffold / partial implementation state.

It already has:

- working auth flow to Superset,
- working batch planner,
- working per-batch execution for at least one real batch,
- result capture using the successful UI-manual SQL Lab flow (`/execute` -> `resultsKey` -> `/results`),
- merged snapshot storage to JSON and DuckDB,
- SQLite-backed metadata for core resources.

It does **not yet** have the full anomaly/rule persistence pipeline implemented.

---

## 2. Current architecture

### Runtime layers

1. **Auth/bootstrap**
   - `src/app/engine/superset_auth.py`
   - Handles login, waits for `/superset/welcome`, then opens SQL Lab in a **new tab**.
   - This part was buggy earlier and is now fixed/tested.

2. **Superset query execution**
   - `src/app/engine/superset_client.py`
   - Backend-first execution wrapper with UI fallback.

3. **UI fallback execution**
   - `src/app/engine/superset_ui_runner.py`
   - Uses Camoufox stealth browser via `browser_factory` to interact with SQL Lab.
   - Requires `camoufox fetch` once; persistent profile lives in `.camoufox-profile/` (override with `SUPERSET_BROWSER_PROFILE_DIR`).
   - This is the most sensitive part of the project.
   - Most debugging time has gone here.

4. **Planning / fan-out**
   - `src/app/engine/query_planner.py`
   - Generates one SQL per batch, not one `IN (...)` query.

5. **Merge**
   - `src/app/engine/merge_engine.py`
   - Python-side merge/upsert by `identity_key`.

6. **Job execution**
   - `src/app/services/jobs.py`
   - Builds runs, executes batches, writes artifacts, writes merged snapshot.

7. **API layer**
   - `src/app/api/resources.py`
   - Core CRUD and execute endpoint.

8. **Metadata DB**
   - SQLite through SQLModel.
   - `src/app/db.py`, `src/app/models.py`

---

## 3. What is currently working

### Verified working behavior

These statements are backed by direct runtime evidence and/or green tests.

#### 3.1 Auth and navigation
- Login starts from `/login` without `/next`
- SSO button is clicked
- After successful login, the app waits for `/superset/welcome`
- SQL Lab opens in a **new tab**

#### 3.2 Batch planning semantics
- Multi-value filters for row-limit bypass are **fan-out**, not one `IN (...)`
- Example batch list: `01`, `02`, `03`, `04`, `71`
- Each batch becomes its own rendered SQL statement

#### 3.3 Single-batch real execution
- Batch `01` has been proven to execute correctly through the UI-manual-compatible path
- Correct result for `01` was observed as:
  - `row_count = 365`
  - `KODE_KAB = '01'`

#### 3.4 Snapshot storage
- Per-run JSON debug snapshot still exists:
  - `artifacts/snapshots/<run_id>/dataset.json`
- Shared queryable DuckDB dataset now exists:
  - `data/dataset.duckdb`
- DuckDB table name:
  - `snapshot_data`

#### 3.5 Identity key generation
- `identity_key` is already formed before merge and before final storage
- Built from source result columns:
  - `KODE_PROV`
  - `KODE_KAB`
  - `KODE_KEC`
  - `KODE_DESA`
  - `SLS`
  - `SUBSLS`
  - `NKS`
  - `DSRT`
  - `NO_ART`

Formula:

```text
KODE_PROV + KODE_KAB + KODE_KEC + KODE_DESA + SLS + SUBSLS + "-" + NKS + "-" + DSRT + "-" + NO_ART
```

Example:

```text
6571030004002800-20250434-10-3
```

---

## 4. What is NOT fully solved yet

### 4.1 Full 5-batch end-to-end reliability

Single batch is proven.

Full multi-batch execution is much better than before, but still the most fragile area.

Key reasons:
- SQL Lab is stateful
- UI fallback depends on live Camoufox/browser response timing
- Batch result correlation is sensitive
- `/results/` responses and `updated_since` metadata can arrive out of order

### 4.2 Anomaly / rules pipeline

Design exists.

Implementation is only partial scaffolding.

Not yet fully done:
- SQL anomaly rules reading from DuckDB snapshot
- canonical `FindingRuleHit` persistence from real SQL results
- identity findings inbox backed by real findings from real snapshots

### 4.3 UI maturity

There is a thin UI and API surface, but it is not feature-complete.

---

## 5. Current storage semantics

This is important and must not be misunderstood.

### SQLite
- Used for metadata only
- jobs, runs, snapshots, rules, findings metadata, etc.

### JSON snapshot
- Per-run debug artifact
- Human-readable
- Good for inspection/debugging

### DuckDB snapshot
- Shared current dataset
- Located at:
  - `data/dataset.duckdb`
- Table:
  - `snapshot_data`

### Replace vs upsert behavior

#### Python merge step
- Batches are merged in Python
- Merge/upsert key is usually `identity_key`
- Current merge semantics = **last write wins** per key inside one run

#### DuckDB write step
- Final DuckDB table is currently **replaced fully** each run
- It is NOT doing row-level upsert across runs

Meaning:
- within one run: Python does key-based merge
- across runs: DuckDB `snapshot_data` becomes the latest full dataset

So treat `data/dataset.duckdb` as:

> the latest working queryable snapshot

not a historical snapshot warehouse.

---

## 6. SQL / batch strategy rules

### Important invariant

For row-limit bypass use cases, filters must fan out into separate SQL runs.

Do NOT collapse batch values into one query with `IN (...)` when the purpose is to bypass SQL Lab row caps.

Correct pattern:

```sql
WHERE art.level_2_code='{{ level_2_code }}'
```

with batching strategy:

```json
{
  "type": "explicit_list",
  "param": "level_2_code",
  "values": ["01", "02", "03", "04", "71"]
}
```

That means 5 real queries, not 1 query.

### Current sample-query note

There are helper scripts and QA scripts that may still use a simplified `Link` projection like:

```sql
'LINK' AS Link
```

This was temporarily used during debugging to avoid a malformed one-argument `CONCAT('LINK')` bug in helper scripts.

The intended real expression is:

```sql
CONCAT('<a href="https://fasih-sm.bps.go.id/survey-collection/assignment-detail/', art.assignment_id, '/9b637b4c-2839-4a16-9023-1a62c364572b" target="_blank">Link Assignment</a>') AS Link
```

This multi-argument `CONCAT(...)` is valid.

---

## 7. Most important files

### Core runtime
- `src/app/engine/browser_factory.py`
- `src/app/engine/superset_auth.py`
- `src/app/engine/superset_client.py`
- `src/app/engine/superset_ui_runner.py`

### Planning and merge
- `src/app/engine/query_planner.py`
- `src/app/engine/merge_engine.py`

### Execution and storage
- `src/app/services/jobs.py`
- `src/app/services/runs.py`
- `src/app/services/findings.py`

### API
- `src/app/api/resources.py`
- `src/app/api/runs.py`
- `src/app/api/findings.py`

### Models / DB
- `src/app/models.py`
- `src/app/db.py`

### Debug / manual QA scripts
- `manual_qa_single_batch.py`
- `manual_qa_sequential_batches.py`
- `manual_trace_execute_request.py`
- `manual_trace_execute_response.py`
- `manual_qa_inspect_editor.py`

---

## 8. High-value debugging lessons already learned

These took a lot of time. Do not rediscover them from scratch.

### 8.1 The old bugs that are already solved
- stale visible result reuse
- auth flow stopping too early on cookie instead of waiting for welcome
- SQL Lab should open in a **new tab** after welcome
- editor mismatch must fail explicitly, not silently continue

### 8.2 The runtime path that finally worked for batch `01`
The successful path is:

1. login
2. reach `/superset/welcome`
3. open new SQL Lab tab
4. update SQL editor
5. click Run
6. `/api/v1/sqllab/execute/` returns query metadata
7. `resultsKey` becomes available
8. `/api/v1/sqllab/results/?q=(key:'...',rows:10000)` returns final rows
9. use `/results/` data as source of truth

### 8.3 The biggest multi-batch pain point
The main historical bug pattern was **off-by-one batch ownership**:
- batch `02` returning batch `01` rows
- batch `03` returning batch `02` rows
- etc.

This came from:
- shared SQL Lab page state across batches
- stale `/results/` traffic arriving early
- weak correlation between the active batch and accepted result payloads
- metadata (`updated_since`) being misread as real rows

### 8.4 Current recommendation for stability
If multi-batch UI fallback remains flaky, prefer:

> one fresh SQL Lab page/tab per batch

That reduces state bleed much more than trying to reuse one page forever.

---

## 9. Existing manual QA scripts and what they are for

### `manual_qa_single_batch.py`
Use this first.

Purpose:
- prove one batch end-to-end
- capture `row_count`
- capture `duckdb_artifact_path`
- inspect `batches.json`

### `manual_qa_sequential_batches.py`
Use this for multi-batch debugging.

Purpose:
- run batches sequentially one by one
- print `BATCH_START`, `BATCH_RESULT`, and stage telemetry
- identify which batch stalls or gets wrong ownership

### `manual_trace_execute_request.py`
Use to capture the actual outgoing `/execute/` request body from the live UI flow.

This was critical to learn which fields the successful manual UI path includes.

### `manual_trace_execute_response.py`
Use to capture the actual `/execute/` response and post-execute network behavior.

### `manual_qa_inspect_editor.py`
Use to inspect live Ace editor visibility/focus state on the SQL Lab page.

---

## 10. Known-good runtime evidence

These runtime facts are already validated and should be treated as established unless new evidence contradicts them.

### One-batch `01`
- query is really sent as `art.level_2_code='01'`
- one-batch execution returned:
  - `row_count = 365`
  - `KODE_KAB = '01'`
- DuckDB artifact queried successfully:

```sql
SELECT COUNT(*) FROM snapshot_data;
SELECT KODE_KAB, COUNT(*) FROM snapshot_data GROUP BY KODE_KAB;
```

Runtime proof observed:

```text
ROW_COUNT=365
KAB_COUNTS=[["01", 365]]
```

---

## 11. Recommended next development steps

In order:

### Next safest step
Implement SQL anomaly execution directly against:

- `data/dataset.duckdb`
- table: `snapshot_data`

This is the natural next layer now that queryable snapshot storage is working.

### After that
Persist canonical findings:
- `FindingRuleHit`
- grouped identity findings

### Then
Expose those findings in the web UI.

---

## 12. Ground rules for the next developer/agent

1. Do not trust visible SQL Lab tables as the source of truth if `/results/` is available.
2. Treat `updated_since` as metadata only, never as row data.
3. Be very careful with reused Camoufox page state across batches.
4. Prefer direct runtime traces over assumptions when debugging Superset behavior.
5. Keep `data/dataset.duckdb` as the canonical current queryable dataset.
6. Keep JSON artifacts as debug output, not the primary SQL source.
7. If changing batch flow, validate result ownership by checking `KODE_KAB` against the requested batch.

---

## 13. Fast commands to re-verify core behavior

### Focused execution tests
```bash
python -m pytest superset_sqllab_platform/tests/test_job_execution.py -v
```

### Focused UI tests
```bash
python -m pytest superset_sqllab_platform/tests/test_ui_runner.py -v
```

### Full suite
```bash
python -m pytest superset_sqllab_platform/tests -v
```

### One-batch live QA
```bash
python superset_sqllab_platform/manual_qa_single_batch.py
```

### Query the current DuckDB dataset
```python
import duckdb

con = duckdb.connect(r"E:\Python Projects\Scraping Fasih\superset_sqllab_platform\data\dataset.duckdb")
print(con.execute("SELECT COUNT(*) FROM snapshot_data").fetchone())
print(con.execute("SELECT KODE_KAB, COUNT(*) FROM snapshot_data GROUP BY KODE_KAB ORDER BY KODE_KAB").fetchall())
con.close()
```

---

## 14. Current truth in one sentence

The platform can now authenticate to Superset, run at least one real batch correctly, merge results in Python, and persist the merged dataset to a queryable DuckDB file at `data/dataset.duckdb`; the next meaningful work is to run anomaly SQL on that dataset and persist findings.

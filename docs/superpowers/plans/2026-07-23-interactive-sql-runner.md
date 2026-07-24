# Interactive SQL Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zero-argument numbered terminal wizard for `scripts/run_sql_folder.py` with safe defaults and target-aware resume.

**Architecture:** Keep existing `run_paginate_mode` / `run_folder_mode`. Add pure helpers for naming + discovery, injectable prompt I/O for tests, and a thin `run_interactive_menu()` that builds `argparse.Namespace` then delegates. Persist `source_path` / `source_kind` in pagination `progress.json` so resume can match a SQL file or folder.

**Tech Stack:** Python 3 stdlib (`argparse`, `dataclasses`, `datetime`, `json`, `pathlib`, `sys`), existing FakeRunner test pattern, pytest.

## Global Constraints

- Stdlib-only prompts; no rich/curses/prompt-toolkit.
- Interactive defaults: auto login, UI mode, cursor `assignment_id`, row cap 1000, sleep 0.8s; folder retry 2 + resume on.
- Output path: `artifacts/runs/YYYY-MM-DD_HHMMSS_<sanitized-target-name>` with numeric suffix on collision.
- Explicit CLI args keep current noninteractive path.
- Noninteractive stdin + zero args → help + exit 2.
- Indonesian user-facing wizard messages.
- Do not recover legacy SQL-only page folders without `progress.json` source identity.
- Shortest diff; no runner rewrite.

---

### Task 1: Output naming + resume discovery helpers

**Files:**
- Modify: `scripts/run_sql_folder.py`
- Create: `tests/test_run_sql_folder_interactive.py`

**Interfaces:**
- Produces:
  - `sanitize_target_name(name: str) -> str`
  - `build_default_output_dir(target: Path, *, now: datetime | None = None, runs_dir: Path | None = None) -> Path`
  - `@dataclass(frozen=True) ResumeCandidate` with fields: `output_dir: Path`, `source_path: Path`, `source_kind: str`, `page_index: int`, `last_cursor: str | None`, `progress_path: Path`, `mtime: float`
  - `find_resume_candidates(runs_dir: Path, target: Path | None = None) -> list[ResumeCandidate]`
  - Matching: resolve `source_path` against cwd/ROOT when relative; match by resolved path equality; exclude `complete=True`, corrupt JSON, missing source fields; sort newest mtime first.

- [ ] **Step 1: Write failing tests for naming + discovery**

```python
from datetime import datetime
from pathlib import Path
import json
import scripts.run_sql_folder as m

def test_build_default_output_dir_uses_timestamp_and_sanitized_name(tmp_path: Path) -> None:
    target = tmp_path / "My Query!.sql"
    target.write_text("select 1", encoding="utf-8")
    out = m.build_default_output_dir(
        target, now=datetime(2026, 7, 23, 15, 4, 5), runs_dir=tmp_path / "runs"
    )
    assert out == tmp_path / "runs" / "2026-07-23_150405_My_Query"

def test_build_default_output_dir_adds_suffix_on_collision(tmp_path: Path) -> None:
    target = tmp_path / "q.sql"
    target.write_text("select 1", encoding="utf-8")
    runs = tmp_path / "runs"
    first = m.build_default_output_dir(target, now=datetime(2026, 7, 23, 15, 4, 5), runs_dir=runs)
    first.mkdir(parents=True)
    second = m.build_default_output_dir(target, now=datetime(2026, 7, 23, 15, 4, 5), runs_dir=runs)
    assert second.name.endswith("_2")

def test_find_resume_candidates_filters_complete_corrupt_and_mismatched(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    sql = tmp_path / "target.sql"
    sql.write_text("select 1", encoding="utf-8")
    other = tmp_path / "other.sql"
    other.write_text("select 2", encoding="utf-8")

    good = runs / "a"
    good.mkdir(parents=True)
    (good / "progress.json").write_text(
        json.dumps({
            "complete": False,
            "source_path": str(sql.resolve()),
            "source_kind": "file",
            "page_index": 3,
            "last_cursor": "c3",
        }),
        encoding="utf-8",
    )
    done = runs / "b"
    done.mkdir()
    (done / "progress.json").write_text(
        json.dumps({
            "complete": True,
            "source_path": str(sql.resolve()),
            "source_kind": "file",
            "page_index": 9,
            "last_cursor": "done",
        }),
        encoding="utf-8",
    )
    bad = runs / "c"
    bad.mkdir()
    (bad / "progress.json").write_text("{not-json", encoding="utf-8")
    mismatch = runs / "d"
    mismatch.mkdir()
    (mismatch / "progress.json").write_text(
        json.dumps({
            "complete": False,
            "source_path": str(other.resolve()),
            "source_kind": "file",
            "page_index": 1,
            "last_cursor": "x",
        }),
        encoding="utf-8",
    )

    found = m.find_resume_candidates(runs, target=sql)
    assert len(found) == 1
    assert found[0].last_cursor == "c3"
    assert found[0].page_index == 3
```

- [ ] **Step 2: Run tests — expect fail (missing symbols)**

```bash
pytest tests/test_run_sql_folder_interactive.py -v
```

- [ ] **Step 3: Implement helpers in `scripts/run_sql_folder.py`**

```python
from dataclasses import dataclass
from datetime import datetime

def sanitize_target_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return cleaned or "run"

def build_default_output_dir(
    target: Path,
    *,
    now: datetime | None = None,
    runs_dir: Path | None = None,
) -> Path:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    base_name = sanitize_target_name(target.stem if target.suffix.lower() == ".sql" else target.name)
    root = runs_dir or (ROOT / "artifacts" / "runs")
    candidate = root / f"{stamp}_{base_name}"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        alt = root / f"{stamp}_{base_name}_{suffix}"
        if not alt.exists():
            return alt
        suffix += 1

@dataclass(frozen=True)
class ResumeCandidate:
    output_dir: Path
    source_path: Path
    source_kind: str
    page_index: int
    last_cursor: str | None
    progress_path: Path
    mtime: float

def _resolve_source_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (ROOT / path).resolve()

def find_resume_candidates(runs_dir: Path, target: Path | None = None) -> list[ResumeCandidate]:
    if not runs_dir.is_dir():
        return []
    target_resolved = target.resolve() if target is not None else None
    candidates: list[ResumeCandidate] = []
    for progress_path in runs_dir.glob("*/progress.json"):
        payload = _load_progress(progress_path)
        if not payload or payload.get("complete"):
            continue
        source_raw = payload.get("source_path")
        source_kind = payload.get("source_kind")
        if not isinstance(source_raw, str) or not isinstance(source_kind, str):
            continue
        if source_kind not in {"file", "folder"}:
            continue
        source_path = _resolve_source_path(source_raw)
        if target_resolved is not None and source_path != target_resolved:
            continue
        last_cursor = payload.get("last_cursor")
        if last_cursor is not None:
            last_cursor = str(last_cursor)
        try:
            page_index = int(payload.get("page_index") or 0)
            mtime = progress_path.stat().st_mtime
        except (TypeError, ValueError, OSError):
            continue
        candidates.append(
            ResumeCandidate(
                output_dir=progress_path.parent,
                source_path=source_path,
                source_kind=source_kind,
                page_index=page_index,
                last_cursor=last_cursor,
                progress_path=progress_path,
                mtime=mtime,
            )
        )
    candidates.sort(key=lambda item: item.mtime, reverse=True)
    return candidates
```

- [ ] **Step 4: Re-run tests — expect pass**

```bash
pytest tests/test_run_sql_folder_interactive.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/run_sql_folder.py tests/test_run_sql_folder_interactive.py
git commit -m "feat(sql-runner): add interactive output naming and resume discovery"
```

---

### Task 2: Persist source identity in pagination progress

**Files:**
- Modify: `scripts/run_sql_folder.py` (`run_paginate_mode` progress writes)
- Modify: `tests/test_run_sql_folder_paginate.py`

**Interfaces:**
- Consumes: existing `_write_progress`
- Produces: every progress write includes:
  - `source_path`: absolute string of `args.sql_file` when present, else label path pseudo `"inline:<label>"` is NOT used — only file/folder targets for interactive; for CLI raw `--sql`, store `source_kind="inline"` and `source_path=args.label or "paginated_query"` so discovery can ignore unless needed.
  - Spec requires file/folder discovery; interactive never uses raw `--sql`.
  - Decision: if `args.sql_file`: `source_kind="file"`, `source_path=str(Path(args.sql_file).resolve())`. Else: `source_kind="inline"`, `source_path=label` (excluded from latest/target resume by kind filter).

- [ ] **Step 1: Extend paginate tests**

Assert `progress["source_path"]` and `progress["source_kind"] == "file"` after error persist test.

- [ ] **Step 2: Fail then implement progress payload merge helper**

```python
def _source_identity(args: argparse.Namespace, label: str) -> dict[str, str]:
    if args.sql_file:
        return {
            "source_kind": "file",
            "source_path": str(Path(args.sql_file).resolve()),
        }
    return {"source_kind": "inline", "source_path": label}
```

Include identity in every `_write_progress` call inside `run_paginate_mode`.

- [ ] **Step 3: Run**

```bash
pytest tests/test_run_sql_folder_paginate.py tests/test_run_sql_folder_interactive.py -v
```

- [ ] **Step 4: Commit**

```bash
git add scripts/run_sql_folder.py tests/test_run_sql_folder_paginate.py
git commit -m "feat(sql-runner): persist source identity in pagination progress"
```

---

### Task 3: Interactive menu + zero-arg routing

**Files:**
- Modify: `scripts/run_sql_folder.py` (`main`, `run_interactive_menu`, builders)
- Modify: `tests/test_run_sql_folder_interactive.py`

**Interfaces:**
- Produces:
  - `DEFAULT_INTERACTIVE_SLEEP = 0.8`
  - `build_interactive_paginate_args(sql_file: Path, output_dir: Path, **overrides) -> argparse.Namespace`
  - `build_interactive_folder_args(sql_dir: Path, output_dir: Path, **overrides) -> argparse.Namespace`
  - `run_interactive_menu(*, input_fn=input, output_fn=print, runs_dir: Path | None = None) -> int`
  - `main()`: if `len(sys.argv) == 1` and not `sys.stdin.isatty()` → print help via parser + return 2; if `len(sys.argv) == 1` and tty → `run_interactive_menu()`; else parse_args as today.

Menu (Indonesian):

```
=== Superset SQL Runner ===
1. Jalankan file SQL (paginate)
2. Jalankan folder SQL
3. Lanjutkan run terakhir
0. Keluar
```

File flow:
1. Prompt path; reject missing / non-`.sql` with Indonesian error; re-prompt.
2. Look for `find_resume_candidates(runs, target)`.
3. If candidates: show newest summary; prompt `1=Lanjutkan 2=Mulai baru 0=Batal`.
4. Resume → reuse candidate `output_dir` (auto progress resume, no `--start-after`).
5. Fresh → `build_default_output_dir(target)`.
6. Call `run_paginate_mode(args)`.

Folder flow:
1. Prompt dir; require exists + at least one `*.sql` (non-recursive); Indonesian errors.
2. Resume candidates with `source_kind=folder` match.
3. Fresh uses timestamped output.
4. Call `run_folder_mode(args)`.
5. For folder progress identity: write is owned by folder runner state, not pagination progress. Interactive latest resume for folders uses a thin `progress.json` written by interactive layer OR only supports pagination latest. Spec says latest resume identifies source SQL — file is primary; folder resume via target selection is required. For folder, write `output_dir/progress.json` with `source_kind=folder`, `complete=False` before run only if folder runner lacks it — **minimal:** interactive folder resume uses same `progress.json` convention written by a small wrapper after selection if missing, and marks complete based on folder summary if available. YAGNI: folder resume = reuse prior `output_dir` when candidate exists; discovery requires progress written at start of interactive folder run:

```python
_write_progress(output_dir / "progress.json", {
    "complete": False,
    "source_kind": "folder",
    "source_path": str(sql_dir.resolve()),
    "page_index": 0,
    "last_cursor": None,
})
```

On successful folder exit code 0, set `complete=True`. On failure leave incomplete.

Latest resume:
1. `find_resume_candidates(runs)` all incomplete with identity.
2. Empty → Indonesian message, return to menu (loop).
3. Show top candidate details; confirm then route by `source_kind`.

Invalid menu input → Indonesian error, re-prompt. Exit 0 returns 0.

Defaults on built Namespace (paginate):

```python
Namespace(
    sql_dir=None,
    sql_file=str(sql_file),
    sql=None,
    paginate=True,
    cursor_column="assignment_id",
    order="ASC",
    start_after=None,
    max_pages=10_000,
    output_dir=str(output_dir),
    label=None,
    base_url="https://fasih-dashboard.bps.go.id",
    sql_lab_url="https://fasih-dashboard.bps.go.id/superset/sqllab/",
    manual_login=False,
    superset_mode="ui",
    recursive=False,
    no_resume=False,
    retry=2,
    retry_backoff=3.0,
    sleep=0.8,
    fail_fast=False,
    limit=None,
    only=None,
    row_cap=DEFAULT_ROW_CAP,
    no_xlsx=False,
    allow_errors=False,
)
```

- [ ] **Step 1: Write interactive tests with scripted input_fn/output_fn**

Cover:
- `test_interactive_file_fresh_builds_output_and_calls_paginate`
- `test_interactive_file_resume_reuses_output_dir`
- `test_interactive_invalid_menu_then_exit`
- `test_interactive_latest_resume_empty_message`
- `test_main_zero_args_non_tty_returns_2`
- `test_main_with_args_skips_menu`

Monkeypatch `run_paginate_mode` / `run_folder_mode` to capture Namespace.

- [ ] **Step 2: Implement menu + main routing**

- [ ] **Step 3: Run focused suite**

```bash
pytest tests/test_run_sql_folder_interactive.py tests/test_run_sql_folder_paginate.py -v
```

- [ ] **Step 4: Commit**

```bash
git add scripts/run_sql_folder.py tests/test_run_sql_folder_interactive.py
git commit -m "feat(sql-runner): add zero-arg interactive SQL runner menu"
```

---

### Task 4: Verification

- [ ] **Step 1: Focused tests green**

```bash
pytest tests/test_run_sql_folder_interactive.py tests/test_run_sql_folder_paginate.py -v
```

- [ ] **Step 2: Smoke zero-arg non-tty**

```bash
python scripts/run_sql_folder.py < NUL
```

Expected: help-ish / need args message, exit 2.

- [ ] **Step 3: Adversarial self-check**
  - Resume never renumbers pages (existing offset logic untouched).
  - Start fresh never reuses old output_dir.
  - Explicit CLI path unchanged when argv present.
  - No third-party prompt deps.

---

## Spec coverage checklist

| Spec item | Task |
|---|---|
| Numbered zero-arg menu | 3 |
| File defaults UI/auto/assignment_id/1000/0.8 | 3 |
| Folder defaults UI/auto/resume/retry2/0.8 | 3 |
| Timestamped output path | 1 |
| Target-aware resume offer | 1+3 |
| Resume reuses dir/cursor | 2+3 |
| Start fresh new dir | 1+3 |
| Latest resume details | 3 |
| Empty latest → message + menu | 3 |
| Invalid/missing path re-prompt | 3 |
| Explicit CLI unchanged | 3 |
| Noninteractive stdin exit 2 | 3 |
| Collision suffix | 1 |
| source_path/source_kind | 2 |
| Ignore complete/corrupt/legacy | 1 |

## Placeholder scan

None intentional.

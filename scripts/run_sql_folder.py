from __future__ import annotations

import argparse
import re
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from app.engine.superset_query_runner import SupersetQueryRunner
from app.services.sql_folder_runner import (
    DEFAULT_ROW_CAP,
    default_folder_output_dir,
    run_sql_folder,
)
from app.services.sql_keyset_pagination import run_keyset_paginated_query


def _build_runner(args: argparse.Namespace) -> SupersetQueryRunner:
    return SupersetQueryRunner(
        base_url=args.base_url,
        sql_lab_url=args.sql_lab_url,
        manual_login=args.manual_login,
        mode=args.superset_mode,
        debug_callback=lambda event: print("STAGE=" + str(event.get("stage")), flush=True)
        if isinstance(event, dict)
        else None,
    )


def run_folder_mode(args: argparse.Namespace) -> int:
    sql_dir = Path(args.sql_dir)
    output_dir = Path(args.output_dir) if args.output_dir else default_folder_output_dir(ROOT, args.label)
    only = [part.strip() for part in args.only.split(",")] if args.only else None

    def progress(result, index, total):  # type: ignore[no-untyped-def]
        flag = " TRUNCATED" if result.truncated else ""
        print(
            f"[{index}/{total}] {result.status.upper():8} {result.query_name} "
            f"rows={result.row_count} cols={result.col_count} attempts={result.attempts}{flag}",
            flush=True,
        )
        if result.status == "failed":
            print(f"    error={result.error_kind}: {result.error}", flush=True)

    print(f"SQL_FOLDER_DIR={sql_dir}", flush=True)
    print(f"SQL_FOLDER_OUTPUT={output_dir}", flush=True)
    print(f"SQL_FOLDER_MODE={args.superset_mode} resume={not args.no_resume}", flush=True)

    runner = _build_runner(args)
    try:
        result = run_sql_folder(
            sql_dir=sql_dir,
            output_dir=output_dir,
            query_runner=runner,
            recursive=args.recursive,
            resume=not args.no_resume,
            retry=args.retry,
            retry_backoff_seconds=args.retry_backoff,
            fail_fast=args.fail_fast,
            limit=args.limit,
            only=only,
            row_cap=args.row_cap,
            write_xlsx=not args.no_xlsx,
            sleep_between_seconds=args.sleep,
            progress_callback=progress,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"SQL_FOLDER_ERROR={exc}", file=sys.stderr)
        return 2

    print(f"SQL_FOLDER_STATE={result.state_path}", flush=True)
    print(f"SQL_FOLDER_SUMMARY={result.summary_path}", flush=True)
    print(f"SQL_FOLDER_COVERAGE={result.coverage_csv_path}", flush=True)
    print(f"SQL_FOLDER_COMPLETED={result.completed_count}", flush=True)
    print(f"SQL_FOLDER_SKIPPED={result.skipped_count}", flush=True)
    print(f"SQL_FOLDER_FAILED={result.failed_count}", flush=True)
    print(f"SQL_FOLDER_TRUNCATED={result.truncated_count}", flush=True)

    if result.failed_count and not args.allow_errors:
        return 1
    if result.truncated_count and not args.allow_errors:
        return 3
    return 0


def _load_progress(progress_path: Path) -> dict | None:
    if not progress_path.is_file():
        return None
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_progress(progress_path: Path, payload: dict) -> None:
    progress_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_page_csvs(results_dir: Path) -> pd.DataFrame:
    paths = sorted(results_dir.glob("page_*.csv"))
    frames: list[pd.DataFrame] = []
    for path in paths:
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)



DEFAULT_INTERACTIVE_SLEEP = 0.8
DEFAULT_BASE_URL = "https://fasih-dashboard.bps.go.id"
DEFAULT_SQL_LAB_URL = "https://fasih-dashboard.bps.go.id/superset/sqllab/"


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


def _source_identity(args: argparse.Namespace, label: str) -> dict[str, str]:
    if getattr(args, "sql_file", None):
        return {
            "source_kind": "file",
            "source_path": str(Path(args.sql_file).resolve()),
        }
    if getattr(args, "sql_dir", None):
        return {
            "source_kind": "folder",
            "source_path": str(Path(args.sql_dir).resolve()),
        }
    return {"source_kind": "inline", "source_path": label}


def _default_runner_namespace(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "sql_dir": None,
        "sql_file": None,
        "sql": None,
        "paginate": False,
        "cursor_column": "assignment_id",
        "order": "ASC",
        "start_after": None,
        "max_pages": 10_000,
        "output_dir": None,
        "label": None,
        "base_url": DEFAULT_BASE_URL,
        "sql_lab_url": DEFAULT_SQL_LAB_URL,
        "manual_login": False,
        "superset_mode": "ui",
        "recursive": False,
        "no_resume": False,
        "retry": 2,
        "retry_backoff": 3.0,
        "sleep": DEFAULT_INTERACTIVE_SLEEP,
        "fail_fast": False,
        "limit": None,
        "only": None,
        "row_cap": DEFAULT_ROW_CAP,
        "no_xlsx": False,
        "allow_errors": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def build_interactive_paginate_args(sql_file: Path, output_dir: Path, **overrides: object) -> argparse.Namespace:
    return _default_runner_namespace(
        sql_file=str(sql_file),
        paginate=True,
        output_dir=str(output_dir),
        **overrides,
    )


def build_interactive_folder_args(sql_dir: Path, output_dir: Path, **overrides: object) -> argparse.Namespace:
    return _default_runner_namespace(
        sql_dir=str(sql_dir),
        output_dir=str(output_dir),
        **overrides,
    )

_PAGE_INDEX_RE = re.compile(r"^page_(\d+)\.(?:csv|sql)$", re.IGNORECASE)


def _max_existing_page_index(results_dir: Path) -> int:
    """Highest page_NNNN.* index already on disk (0 if none)."""
    if not results_dir.is_dir():
        return 0
    max_index = 0
    for path in results_dir.iterdir():
        match = _PAGE_INDEX_RE.match(path.name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index


def run_paginate_mode(args: argparse.Namespace) -> int:
    """Ad-hoc / non-folder SQL: keyset pagination until exhausted."""
    if args.sql_file:
        base_sql = Path(args.sql_file).read_text(encoding="utf-8")
        label = Path(args.sql_file).stem
    else:
        base_sql = args.sql
        label = args.label or "paginated_query"

    if not base_sql or not base_sql.strip():
        print("SQL_PAGINATE_ERROR=empty SQL", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else default_folder_output_dir(ROOT, args.label or "paginate")
    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir = output_dir / "pages"
    results_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.json"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._-") or "paginated"
    source_meta = _source_identity(args, label)


    start_after = args.start_after
    resume_page_offset = 0
    prior_pages_meta: list[dict] = []
    disk_max_page = _max_existing_page_index(results_dir)
    prior = None if args.no_resume else _load_progress(progress_path)

    if start_after is None and prior and not prior.get("complete") and prior.get("last_cursor") is not None:
        start_after = prior["last_cursor"]
        resume_page_offset = max(int(prior.get("page_index") or 0), disk_max_page)
        prior_pages = prior.get("pages")
        if isinstance(prior_pages, list):
            prior_pages_meta = [p for p in prior_pages if isinstance(p, dict)]
        print(
            f"SQL_PAGINATE_RESUME last_cursor={start_after!r} after_page={resume_page_offset} "
            f"(disk_max={disk_max_page})",
            flush=True,
        )
    elif start_after is not None:
        # Manual --start-after: never renumber from page_0001 over existing files.
        if prior:
            resume_page_offset = int(prior.get("page_index") or 0)
            prior_pages = prior.get("pages")
            if isinstance(prior_pages, list):
                prior_pages_meta = [p for p in prior_pages if isinstance(p, dict)]
        # Disk wins if higher (progress missing/stale after crash overwrite risk).
        resume_page_offset = max(resume_page_offset, disk_max_page)
        print(
            f"SQL_PAGINATE_RESUME start_after={start_after!r} after_page={resume_page_offset} "
            f"(disk_max={disk_max_page})",
            flush=True,
        )

    print(f"SQL_PAGINATE_OUTPUT={output_dir}", flush=True)
    print(f"SQL_PAGINATE_CURSOR={args.cursor_column} page_size={args.row_cap}", flush=True)

    runner = _build_runner(args)
    pages_meta: list[dict] = list(prior_pages_meta)

    def on_page(page):  # type: ignore[no-untyped-def]
        absolute_index = resume_page_offset + page.page_index
        print(
            f"[page {absolute_index}] rows={page.row_count} last_cursor={page.last_cursor!r} "
            f"{'FULL_PAGE' if page.truncated_page else 'partial/final'}",
            flush=True,
        )
        (results_dir / f"page_{absolute_index:04d}.sql").write_text(page.sql, encoding="utf-8")
        if page.dataframe is not None and not page.dataframe.empty:
            page.dataframe.to_csv(results_dir / f"page_{absolute_index:04d}.csv", index=False)
        pages_meta.append(
            {
                "page_index": absolute_index,
                "row_count": page.row_count,
                "last_cursor": None if page.last_cursor is None else str(page.last_cursor),
                "full_page": page.truncated_page,
            }
        )
        _write_progress(
            progress_path,
            {
                "complete": False,
                "cursor_column": args.cursor_column,
                "page_size": args.row_cap,
                "page_index": absolute_index,
                "row_count": page.row_count,
                "last_cursor": None if page.last_cursor is None else str(page.last_cursor),
                "pages": pages_meta,
                **source_meta,
            },
        )

    result = None
    error: Exception | None = None
    try:
        result = run_keyset_paginated_query(
            runner,
            base_sql,
            cursor_column=args.cursor_column,
            page_size=args.row_cap,
            order=args.order,
            max_pages=args.max_pages,
            start_after=start_after,
            sleep_between_pages_seconds=args.sleep,
            progress_callback=on_page,
        )
    except Exception as exc:  # noqa: BLE001
        error = exc
        print(f"SQL_PAGINATE_ERROR={exc}", file=sys.stderr)
    finally:
        close = getattr(runner, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    # Prefer on-disk page CSVs so crash mid-run still yields partial output.
    disk_frame = _merge_page_csvs(results_dir)
    if result is not None and not result.dataframe.empty and disk_frame.empty:
        disk_frame = result.dataframe

    csv_path = output_dir / f"{stem}_all.csv"
    xlsx_path = output_dir / f"{stem}_all.xlsx"
    json_path = output_dir / f"{stem}_all.json"
    partial_csv_path = output_dir / f"{stem}_partial.csv"

    if error is not None:
        if not disk_frame.empty:
            disk_frame.to_csv(partial_csv_path, index=False)
            disk_frame.to_csv(csv_path, index=False)
            disk_frame.to_json(json_path, orient="records", force_ascii=False, indent=2)
            print(f"SQL_PAGINATE_PARTIAL_CSV={partial_csv_path}", flush=True)
            print(f"SQL_PAGINATE_ROWS={len(disk_frame.index)}", flush=True)
        prior = _load_progress(progress_path) or {}
        prior.update(
            {
                "complete": False,
                "error": str(error),
                "total_rows": int(len(disk_frame.index)),
                **source_meta,
            }
        )
        _write_progress(progress_path, prior)
        print(f"SQL_PAGINATE_META={progress_path}", flush=True)
        if prior.get("last_cursor") is not None:
            print(
                f"SQL_PAGINATE_RESUME_HINT --start-after {prior['last_cursor']!r}",
                flush=True,
            )
        return 1

    assert result is not None
    out_frame = disk_frame if not disk_frame.empty else result.dataframe
    out_frame.to_csv(csv_path, index=False)
    out_frame.to_json(json_path, orient="records", force_ascii=False, indent=2)
    if not args.no_xlsx:
        out_frame.to_excel(xlsx_path, index=False, engine="openpyxl")

    meta = {
        "complete": result.complete,
        "cursor_column": result.cursor_column,
        "page_size": result.page_size,
        "total_rows": int(len(out_frame.index)),
        "page_count": len(result.pages) + resume_page_offset,
        "last_cursor": None
        if not pages_meta
        else pages_meta[-1].get("last_cursor"),
        "notes": result.notes,
        "pages": pages_meta or [
            {
                "page_index": resume_page_offset + p.page_index,
                "row_count": p.row_count,
                "last_cursor": None if p.last_cursor is None else str(p.last_cursor),
                "full_page": p.truncated_page,
            }
            for p in result.pages
        ],
        "outputs": {
            "csv": str(csv_path),
            "json": str(json_path),
            "xlsx": None if args.no_xlsx else str(xlsx_path),
        },
    }
    meta_path = output_dir / "pagination_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_progress(
        progress_path,
        {
            "complete": result.complete,
            "cursor_column": result.cursor_column,
            "page_size": result.page_size,
            "page_index": meta["page_count"],
            "last_cursor": meta["last_cursor"],
            "total_rows": meta["total_rows"],
            "pages": meta["pages"],
            **source_meta,
        },
    )

    print(f"SQL_PAGINATE_ROWS={meta['total_rows']}", flush=True)
    print(f"SQL_PAGINATE_PAGES={meta['page_count']}", flush=True)
    print(f"SQL_PAGINATE_COMPLETE={meta['complete']}", flush=True)
    print(f"SQL_PAGINATE_CSV={csv_path}", flush=True)
    print(f"SQL_PAGINATE_META={meta_path}", flush=True)
    for note in result.notes:
        print(f"SQL_PAGINATE_NOTE={note}", flush=True)

    if not result.complete and not args.allow_errors:
        return 4
    return 0


def _prompt_path(prompt: str, *, input_fn: Callable[..., str], output_fn: Callable[..., None]) -> Path | None:
    raw = input_fn(prompt).strip().strip('"')
    if not raw:
        output_fn("Path kosong. Coba lagi.")
        return None
    return Path(raw).expanduser()


def _choose_resume_or_fresh(
    candidates: list[ResumeCandidate],
    *,
    input_fn: Callable[..., str],
    output_fn: Callable[..., None],
) -> str:
    if not candidates:
        return "fresh"
    top = candidates[0]
    output_fn(
        f"Ditemukan run sebelumnya: {top.output_dir} "
        f"(page={top.page_index}, cursor={top.last_cursor!r})"
    )
    output_fn("1. Lanjutkan run sebelumnya")
    output_fn("2. Mulai baru")
    output_fn("0. Batal")
    while True:
        choice = input_fn("Pilih: ").strip()
        if choice == "1":
            return "resume"
        if choice == "2":
            return "fresh"
        if choice == "0":
            return "cancel"
        output_fn("Pilihan tidak valid. Masukkan 1, 2, atau 0.")


def _run_selected_file(
    sql_file: Path,
    *,
    runs_dir: Path,
    input_fn: Callable[..., str],
    output_fn: Callable[..., None],
    resume_candidate: ResumeCandidate | None = None,
) -> int:
    if resume_candidate is not None:
        output_dir = resume_candidate.output_dir
    else:
        candidates = find_resume_candidates(runs_dir, target=sql_file)
        decision = _choose_resume_or_fresh(candidates, input_fn=input_fn, output_fn=output_fn)
        if decision == "cancel":
            return 0
        if decision == "resume":
            output_dir = candidates[0].output_dir
        else:
            output_dir = build_default_output_dir(sql_file, runs_dir=runs_dir)
    args = build_interactive_paginate_args(sql_file, output_dir)
    return run_paginate_mode(args)


def _run_selected_folder(
    sql_dir: Path,
    *,
    runs_dir: Path,
    input_fn: Callable[..., str],
    output_fn: Callable[..., None],
    resume_candidate: ResumeCandidate | None = None,
) -> int:
    if resume_candidate is not None:
        output_dir = resume_candidate.output_dir
    else:
        candidates = find_resume_candidates(runs_dir, target=sql_dir)
        decision = _choose_resume_or_fresh(candidates, input_fn=input_fn, output_fn=output_fn)
        if decision == "cancel":
            return 0
        if decision == "resume":
            output_dir = candidates[0].output_dir
        else:
            output_dir = build_default_output_dir(sql_dir, runs_dir=runs_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.json"
    _write_progress(
        progress_path,
        {
            "complete": False,
            "source_kind": "folder",
            "source_path": str(sql_dir.resolve()),
            "page_index": 0,
            "last_cursor": None,
        },
    )
    args = build_interactive_folder_args(sql_dir, output_dir)
    code = run_folder_mode(args)
    if code == 0:
        prior = _load_progress(progress_path) or {}
        prior.update(
            {
                "complete": True,
                "source_kind": "folder",
                "source_path": str(sql_dir.resolve()),
            }
        )
        _write_progress(progress_path, prior)
    return code


def run_interactive_menu(
    *,
    input_fn: Callable[..., str] = input,
    output_fn: Callable[..., None] = print,
    runs_dir: Path | None = None,
) -> int:
    runs = runs_dir or (ROOT / "artifacts" / "runs")
    while True:
        output_fn("=== Superset SQL Runner ===")
        output_fn("1. Jalankan file SQL (paginate)")
        output_fn("2. Jalankan folder SQL")
        output_fn("3. Lanjutkan run terakhir")
        output_fn("0. Keluar")
        try:
            choice = input_fn("Pilih menu: ").strip()
        except EOFError:
            output_fn("Input berakhir. Keluar.")
            return 2

        if choice == "0":
            return 0

        if choice == "1":
            while True:
                path = _prompt_path("Path file .sql: ", input_fn=input_fn, output_fn=output_fn)
                if path is None:
                    continue
                if not path.is_file():
                    output_fn(f"File tidak ditemukan: {path}")
                    continue
                if path.suffix.lower() != ".sql":
                    output_fn("File harus berekstensi .sql")
                    continue
                return _run_selected_file(path.resolve(), runs_dir=runs, input_fn=input_fn, output_fn=output_fn)

        if choice == "2":
            while True:
                path = _prompt_path("Path folder SQL: ", input_fn=input_fn, output_fn=output_fn)
                if path is None:
                    continue
                if not path.is_dir():
                    output_fn(f"Folder tidak ditemukan: {path}")
                    continue
                sql_files = sorted(path.glob("*.sql"))
                if not sql_files:
                    output_fn("Folder tidak berisi file .sql")
                    continue
                return _run_selected_folder(path.resolve(), runs_dir=runs, input_fn=input_fn, output_fn=output_fn)

        if choice == "3":
            candidates = find_resume_candidates(runs)
            if not candidates:
                output_fn("Belum ada run yang bisa dilanjutkan.")
                continue
            top = candidates[0]
            output_fn(
                f"Run terakhir: kind={top.source_kind} source={top.source_path} "
                f"output={top.output_dir} page={top.page_index} cursor={top.last_cursor!r}"
            )
            try:
                confirm = input_fn("Lanjutkan run ini? [y/N]: ").strip().lower()
            except EOFError:
                output_fn("Input berakhir. Keluar.")
                return 2
            if confirm not in {"y", "ya"}:
                continue
            if top.source_kind == "folder":
                return _run_selected_folder(
                    top.source_path,
                    runs_dir=runs,
                    input_fn=input_fn,
                    output_fn=output_fn,
                    resume_candidate=top,
                )
            return _run_selected_file(
                top.source_path,
                runs_dir=runs,
                input_fn=input_fn,
                output_fn=output_fn,
                resume_candidate=top,
            )

        output_fn("Pilihan tidak valid. Masukkan 1, 2, 3, atau 0.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run Superset SQL Lab batches.\n"
            "1) Folder mode: every pre-batched .sql file (resume/retry).\n"
            "2) Paginate mode: one SQL auto-split by keyset cursor (assignment_id)."
        )
    )
    parser.add_argument("--sql-dir", default=None, help="Folder containing .sql files (folder mode)")
    parser.add_argument("--sql-file", default=None, help="Single .sql file (paginate mode)")
    parser.add_argument("--sql", default=None, help="Raw SQL string (paginate mode)")
    parser.add_argument("--paginate", action="store_true", help="Force keyset pagination mode")
    parser.add_argument(
        "--cursor-column",
        default="assignment_id",
        help="Keyset cursor column/alias present in SELECT (default: assignment_id)",
    )
    parser.add_argument("--order", choices=("ASC", "DESC"), default="ASC")
    parser.add_argument("--start-after", default=None, help="Resume pagination after this cursor value")
    parser.add_argument("--max-pages", type=int, default=10_000)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--label", default=None)
    parser.add_argument("--base-url", default="https://fasih-dashboard.bps.go.id")
    parser.add_argument("--sql-lab-url", default="https://fasih-dashboard.bps.go.id/superset/sqllab/")
    parser.add_argument("--manual-login", action="store_true")
    parser.add_argument("--superset-mode", choices=("auto", "ui"), default="ui")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--retry", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=3.0)
    parser.add_argument("--sleep", type=float, default=0.8)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None, help="Comma-separated file names/stems (folder mode)")
    parser.add_argument("--row-cap", type=int, default=DEFAULT_ROW_CAP, help="SQL Lab row cap / page size (default 1000)")
    parser.add_argument("--no-xlsx", action="store_true")
    parser.add_argument("--allow-errors", action="store_true")
    if len(sys.argv) == 1:
        # Some Windows shells still report isatty() under empty redirect.
        if not sys.stdin.isatty():
            parser.print_help(sys.stderr)
            return 2
        try:
            return run_interactive_menu()
        except EOFError:
            parser.print_help(sys.stderr)
            return 2

    args = parser.parse_args()

    use_paginate = bool(args.paginate or args.sql_file or args.sql)
    if use_paginate and args.sql_dir and not args.paginate:
        # If both given without --paginate, prefer folder mode unless only sql-file/sql set.
        use_paginate = False if args.sql_dir and not (args.sql_file or args.sql) else True

    if use_paginate:
        if not (args.sql_file or args.sql):
            print("SQL_PAGINATE_ERROR=need --sql-file or --sql with --paginate", file=sys.stderr)
            return 2
        return run_paginate_mode(args)

    if not args.sql_dir:
        print("SQL_FOLDER_ERROR=need --sql-dir (folder mode) or --paginate/--sql-file/--sql", file=sys.stderr)
        return 2
    return run_folder_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())

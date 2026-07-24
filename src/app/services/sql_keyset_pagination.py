from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from app.services.sql_folder_runner import DEFAULT_ROW_CAP, is_truncated_result


_LIMIT_RE = re.compile(r"(?is)\blimit\s+\d+\s*;?\s*$")
_SELECT_FROM_RE = re.compile(r"(?is)^\s*select\b(.*?)\bfrom\b")
_AS_ALIAS_RE = re.compile(r"(?is)\bas\s+([A-Za-z_][\w$]*)\s*$")
_IDENT_TAIL_RE = re.compile(r"([A-Za-z_][\w$]*)\s*$")


@dataclass
class PageFetch:
    page_index: int
    sql: str
    row_count: int
    last_cursor: Any | None
    truncated_page: bool
    dataframe: pd.DataFrame | None = None


@dataclass
class PaginatedQueryResult:
    dataframe: pd.DataFrame
    pages: list[PageFetch] = field(default_factory=list)
    complete: bool = True
    cursor_column: str = ""
    page_size: int = DEFAULT_ROW_CAP
    notes: list[str] = field(default_factory=list)


def strip_trailing_limit(sql: str) -> str:
    return _LIMIT_RE.sub("", sql.strip().rstrip(";")).strip()


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def extract_select_output_columns(sql: str) -> list[str]:
    """Derive outer column names/aliases from a base SELECT list.

    SQL Lab on this host rejects SELECT *. Keyset wrapper must project
    explicit columns from the caller's SELECT list.
    """
    match = _SELECT_FROM_RE.search(sql.strip())
    if match is None:
        raise ValueError("base SQL must start with SELECT ... FROM for keyset pagination")

    columns: list[str] = []
    for item in _split_top_level_commas(match.group(1)):
        as_match = _AS_ALIAS_RE.search(item)
        if as_match is not None:
            columns.append(as_match.group(1))
            continue
        ident_match = _IDENT_TAIL_RE.search(item)
        if ident_match is None:
            raise ValueError(f"cannot derive output column from select item: {item}")
        columns.append(ident_match.group(1))

    if not columns:
        raise ValueError("base SQL SELECT list is empty")
    return columns


def _sql_literal(value: Any) -> str:
    if value is None:
        raise ValueError("cursor value is None")
    # numpy / pandas scalar -> python
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # integral floats (e.g. 12.0) keep as int-like without quotes when whole
        if value.is_integer():
            return str(int(value))
        return repr(value)
    # strings / uuids / mixed ids
    text = str(value).replace("'", "''")
    return f"'{text}'"


def build_keyset_page_sql(
    base_sql: str,
    *,
    cursor_column: str,
    last_cursor: Any | None,
    page_size: int = DEFAULT_ROW_CAP,
    order: str = "ASC",
) -> str:
    """
    Wrap a base SELECT as a keyset page.

    Pattern (no SELECT * — blocked by this Superset SQL Lab):
      SELECT col1, col2, ... FROM (
        <base_sql without trailing LIMIT>
      ) AS _page
      WHERE <cursor_column> > <last_cursor>   -- omitted on first page
      ORDER BY <cursor_column> ASC
      LIMIT <page_size>

    Outer columns are derived from the caller's SELECT list aliases.
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if page_size > DEFAULT_ROW_CAP:
        # Never request more than SQL Lab can return.
        page_size = DEFAULT_ROW_CAP

    order_u = order.strip().upper()
    if order_u not in {"ASC", "DESC"}:
        raise ValueError("order must be ASC or DESC")

    inner = strip_trailing_limit(base_sql)
    outer_columns = extract_select_output_columns(inner)
    if cursor_column not in outer_columns:
        raise ValueError(
            f"cursor_column {cursor_column!r} not found in SELECT outputs: {outer_columns}"
        )

    cmp = ">" if order_u == "ASC" else "<"
    where_clause = ""
    if last_cursor is not None:
        where_clause = f"WHERE {cursor_column} {cmp} {_sql_literal(last_cursor)}"

    select_list = ", ".join(outer_columns)
    return (
        f"SELECT {select_list} FROM (\n"
        f"{inner}\n"
        ") AS _page\n"
        f"{where_clause}\n"
        f"ORDER BY {cursor_column} {order_u}\n"
        f"LIMIT {int(page_size)}"
    ).strip() + "\n"


def resolve_cursor_value(dataframe: pd.DataFrame, cursor_column: str) -> Any | None:
    if dataframe.empty:
        return None
    # Case-insensitive column match
    colmap = {str(c).lower(): c for c in dataframe.columns}
    key = cursor_column.lower().split(".")[-1]
    if cursor_column.lower() in colmap:
        series = dataframe[colmap[cursor_column.lower()]]
    elif key in colmap:
        series = dataframe[colmap[key]]
    else:
        raise KeyError(
            f"cursor column '{cursor_column}' not in result columns: {list(dataframe.columns)}"
        )
    return series.iloc[-1]


def run_keyset_paginated_query(
    query_runner: Callable[[str], pd.DataFrame],
    base_sql: str,
    *,
    cursor_column: str = "assignment_id",
    page_size: int = DEFAULT_ROW_CAP,
    order: str = "ASC",
    max_pages: int = 10_000,
    start_after: Any | None = None,
    sleep_between_pages_seconds: float = 0.0,
    progress_callback: Callable[[PageFetch], None] | None = None,
) -> PaginatedQueryResult:
    """
    Fetch all pages for an ad-hoc SQL that may exceed the 1000-row SQL Lab cap.

    Stops when a page returns fewer than ``page_size`` rows (complete),
    or when ``max_pages`` is hit (incomplete — note attached).

    Recommended cursor for SE2026 roster/root pulls:
      root_table.assignment_id  (stable, unique-ish per assignment)
    Ensure the SELECT list includes the bare cursor column alias used here,
    e.g. ``r.assignment_id AS assignment_id``.
    """
    frames: list[pd.DataFrame] = []
    pages: list[PageFetch] = []
    notes: list[str] = []
    last_cursor = start_after
    complete = False
    page_delay = max(0.0, float(sleep_between_pages_seconds))

    for page_index in range(1, max_pages + 1):
        if page_index > 1 and page_delay > 0:
            time.sleep(page_delay)
        page_sql = build_keyset_page_sql(
            base_sql,
            cursor_column=cursor_column,
            last_cursor=last_cursor,
            page_size=page_size,
            order=order,
        )
        frame = query_runner(page_sql)
        if frame is None:
            frame = pd.DataFrame()
        row_count = int(len(frame.index))
        truncated_page = is_truncated_result(row_count, page_size)
        page_last = None
        if row_count:
            page_last = resolve_cursor_value(frame, cursor_column)
            frames.append(frame)
            last_cursor = page_last

        page = PageFetch(
            page_index=page_index,
            sql=page_sql,
            row_count=row_count,
            last_cursor=page_last,
            truncated_page=truncated_page,
            dataframe=frame,
        )
        pages.append(page)
        if progress_callback:
            progress_callback(page)

        if row_count == 0:
            complete = True
            break
        if row_count < page_size:
            complete = True
            break
        # row_count == page_size → more pages may exist; continue

    if not complete:
        notes.append(
            f"stopped at max_pages={max_pages}; last_cursor={last_cursor!r}. "
            "Re-run with start_after=last_cursor to continue."
        )

    dataframe = (
        pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    )
    return PaginatedQueryResult(
        dataframe=dataframe,
        pages=pages,
        complete=complete,
        cursor_column=cursor_column,
        page_size=page_size,
        notes=notes,
    )

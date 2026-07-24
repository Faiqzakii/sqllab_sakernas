from __future__ import annotations

import pandas as pd
import pytest

from app.services.sql_keyset_pagination import (
    build_keyset_page_sql,
    extract_select_output_columns,
    run_keyset_paginated_query,
    strip_trailing_limit,
)


def test_extract_select_output_columns_aliases_and_bare() -> None:
    sql = """
    SELECT
      r.assignment_id AS assignment_id,
      r.level_6_full_code,
      r.nama_kk
    FROM tgr_fd68e454.root_table r
    WHERE r.level_6_full_code LIKE '6503%'
    """
    assert extract_select_output_columns(sql) == [
        "assignment_id",
        "level_6_full_code",
        "nama_kk",
    ]


def test_build_keyset_page_sql_has_no_star() -> None:
    base = """
    SELECT r.assignment_id AS assignment_id, r.nik
    FROM tgr_fd68e454.root_table r
    WHERE r.is_active = 1
    LIMIT 1000;
    """
    first = build_keyset_page_sql(base, cursor_column="assignment_id", last_cursor=None, page_size=1000)
    assert "SELECT *" not in first
    assert first.startswith("SELECT assignment_id, nik FROM (")
    assert "ORDER BY assignment_id ASC" in first
    assert "LIMIT 1000" in first
    assert "WHERE assignment_id" not in first.split(") AS _page", 1)[1]

    nxt = build_keyset_page_sql(base, cursor_column="assignment_id", last_cursor="abc-123", page_size=1000)
    assert "SELECT *" not in nxt
    assert "WHERE assignment_id > 'abc-123'" in nxt


def test_build_keyset_rejects_missing_cursor_column() -> None:
    with pytest.raises(ValueError, match="cursor_column"):
        build_keyset_page_sql(
            "SELECT r.nik FROM t WHERE 1=1",
            cursor_column="assignment_id",
            last_cursor=None,
        )


def test_run_keyset_paginated_query_merges_pages() -> None:
    def runner(sql: str) -> pd.DataFrame:
        import re
        m = re.search(r"assignment_id > (\d+)", sql)
        start = int(m.group(1)) + 1 if m else 1
        end = min(start + 1000, 2501)
        if start >= 2501:
            return pd.DataFrame(columns=["assignment_id", "nik"])
        return pd.DataFrame([{"assignment_id": i, "nik": f"n{i}"} for i in range(start, end)])

    result = run_keyset_paginated_query(
        runner,
        "SELECT assignment_id, nik FROM root_table",
        cursor_column="assignment_id",
        page_size=1000,
    )
    assert len(result.dataframe.index) == 2500
    assert len(result.pages) == 3
    assert result.pages[2].row_count == 500
    assert all("SELECT *" not in p.sql for p in result.pages)



def test_run_keyset_sleeps_between_pages(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.services.sql_keyset_pagination.time.sleep", lambda s: sleeps.append(s))

    def runner(sql: str) -> pd.DataFrame:
        import re

        m = re.search(r"assignment_id > (\d+)", sql)
        start = int(m.group(1)) + 1 if m else 1
        end = min(start + 2, 5)
        if start >= 5:
            return pd.DataFrame(columns=["assignment_id"])
        return pd.DataFrame([{"assignment_id": i} for i in range(start, end)])

    result = run_keyset_paginated_query(
        runner,
        "SELECT assignment_id FROM root_table",
        cursor_column="assignment_id",
        page_size=2,
        sleep_between_pages_seconds=1.5,
    )
    assert len(result.pages) == 3
    assert sleeps == [1.5, 1.5]


def test_page_fetch_includes_dataframe() -> None:
    def runner(sql: str) -> pd.DataFrame:
        return pd.DataFrame([{"assignment_id": 1}, {"assignment_id": 2}])

    result = run_keyset_paginated_query(
        runner,
        "SELECT assignment_id FROM root_table",
        cursor_column="assignment_id",
        page_size=10,
    )
    assert len(result.pages) == 1
    assert result.pages[0].dataframe is not None
    assert list(result.pages[0].dataframe["assignment_id"]) == [1, 2]
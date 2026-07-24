from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import scripts.run_sql_folder as run_sql_folder


def test_paginate_persists_page_csv_and_progress_on_error(tmp_path: Path, monkeypatch) -> None:
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT assignment_id FROM root_table", encoding="utf-8")
    output_dir = tmp_path / "out"

    class FakeRunner:
        def __call__(self, sql: str) -> pd.DataFrame:
            if "assignment_id >" in sql:
                raise RuntimeError("boom after page 1")
            return pd.DataFrame(
                [{"assignment_id": "aaa-1"}, {"assignment_id": "aaa-2"}]
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(run_sql_folder, "_build_runner", lambda args: FakeRunner())

    args = Namespace(
        sql_file=str(sql_file),
        sql=None,
        label=None,
        output_dir=str(output_dir),
        cursor_column="assignment_id",
        row_cap=2,
        order="ASC",
        max_pages=10,
        start_after=None,
        sleep=0.0,
        no_resume=False,
        no_xlsx=True,
        allow_errors=False,
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        manual_login=False,
        superset_mode="ui",
    )

    code = run_sql_folder.run_paginate_mode(args)
    assert code == 1

    page_csv = output_dir / "pages" / "page_0001.csv"
    progress_path = output_dir / "progress.json"
    assert page_csv.is_file()
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["complete"] is False
    assert progress["last_cursor"] == "aaa-2"
    assert progress["page_index"] == 1
    assert progress["source_kind"] == "file"
    assert Path(progress["source_path"]) == sql_file.resolve()
    partial = pd.read_csv(output_dir / "q_partial.csv")
    assert list(partial["assignment_id"]) == ["aaa-1", "aaa-2"]


def test_paginate_auto_resumes_from_progress_json(tmp_path: Path, monkeypatch) -> None:
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT assignment_id FROM root_table", encoding="utf-8")
    output_dir = tmp_path / "out"
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True)
    pd.DataFrame([{"assignment_id": "old-1"}]).to_csv(pages_dir / "page_0001.csv", index=False)
    (output_dir / "progress.json").write_text(
        json.dumps(
            {
                "complete": False,
                "cursor_column": "assignment_id",
                "page_size": 1,
                "page_index": 1,
                "last_cursor": "old-1",
                "pages": [{"page_index": 1, "row_count": 1, "last_cursor": "old-1", "full_page": True}],
            }
        ),
        encoding="utf-8",
    )

    seen_sql: list[str] = []

    class FakeRunner:
        def __call__(self, sql: str) -> pd.DataFrame:
            seen_sql.append(sql)
            return pd.DataFrame([{"assignment_id": "new-2"}])

        def close(self) -> None:
            return None

    monkeypatch.setattr(run_sql_folder, "_build_runner", lambda args: FakeRunner())

    args = Namespace(
        sql_file=str(sql_file),
        sql=None,
        label=None,
        output_dir=str(output_dir),
        cursor_column="assignment_id",
        row_cap=1,
        order="ASC",
        max_pages=1,
        start_after=None,
        sleep=0.0,
        no_resume=False,
        no_xlsx=True,
        allow_errors=True,
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        manual_login=False,
        superset_mode="ui",
    )

    code = run_sql_folder.run_paginate_mode(args)
    assert code == 0
    assert any("assignment_id > 'old-1'" in sql for sql in seen_sql)
    assert (pages_dir / "page_0002.csv").is_file()
    progress = json.loads((output_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["last_cursor"] == "new-2"
    assert progress["page_index"] == 2


def test_start_after_appends_after_existing_pages(tmp_path: Path, monkeypatch) -> None:
    sql_file = tmp_path / "q.sql"
    sql_file.write_text("SELECT assignment_id FROM root_table", encoding="utf-8")
    output_dir = tmp_path / "out"
    pages_dir = output_dir / "pages"
    pages_dir.mkdir(parents=True)
    # Pretend pages 1..14 already exist; resume must not overwrite page_0001.
    for idx in range(1, 15):
        (pages_dir / f"page_{idx:04d}.sql").write_text(f"-- page {idx}", encoding="utf-8")
        pd.DataFrame([{"assignment_id": f"old-{idx}"}]).to_csv(
            pages_dir / f"page_{idx:04d}.csv", index=False
        )

    seen_sql: list[str] = []

    class FakeRunner:
        def __call__(self, sql: str) -> pd.DataFrame:
            seen_sql.append(sql)
            return pd.DataFrame([{"assignment_id": "new-15"}])

        def close(self) -> None:
            return None

    monkeypatch.setattr(run_sql_folder, "_build_runner", lambda args: FakeRunner())

    args = Namespace(
        sql_file=str(sql_file),
        sql=None,
        label=None,
        output_dir=str(output_dir),
        cursor_column="assignment_id",
        row_cap=1,
        order="ASC",
        max_pages=1,
        start_after="cursor-from-page-14",
        sleep=0.0,
        no_resume=False,
        no_xlsx=True,
        allow_errors=True,
        base_url="https://example.test",
        sql_lab_url="https://example.test/superset/sqllab/",
        manual_login=False,
        superset_mode="ui",
    )

    code = run_sql_folder.run_paginate_mode(args)
    assert code == 0
    assert any("assignment_id > 'cursor-from-page-14'" in sql for sql in seen_sql)
    # Existing early pages untouched; new page continues index.
    assert (pages_dir / "page_0001.sql").read_text(encoding="utf-8") == "-- page 1"
    assert (pages_dir / "page_0014.csv").is_file()
    assert (pages_dir / "page_0015.csv").is_file()
    assert list(pd.read_csv(pages_dir / "page_0015.csv")["assignment_id"]) == ["new-15"]

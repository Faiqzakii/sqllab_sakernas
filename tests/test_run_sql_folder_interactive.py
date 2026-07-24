from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import scripts.run_sql_folder as run_sql_folder


def test_build_default_output_dir_uses_timestamp_and_sanitized_name(tmp_path: Path) -> None:
    target = tmp_path / "My Query!.sql"
    target.write_text("select 1", encoding="utf-8")
    out = run_sql_folder.build_default_output_dir(
        target, now=datetime(2026, 7, 23, 15, 4, 5), runs_dir=tmp_path / "runs"
    )
    assert out == tmp_path / "runs" / "2026-07-23_150405_My_Query"


def test_build_default_output_dir_adds_suffix_on_collision(tmp_path: Path) -> None:
    target = tmp_path / "q.sql"
    target.write_text("select 1", encoding="utf-8")
    runs = tmp_path / "runs"
    first = run_sql_folder.build_default_output_dir(
        target, now=datetime(2026, 7, 23, 15, 4, 5), runs_dir=runs
    )
    first.mkdir(parents=True)
    second = run_sql_folder.build_default_output_dir(
        target, now=datetime(2026, 7, 23, 15, 4, 5), runs_dir=runs
    )
    assert second.name.endswith("_2")
    assert second != first


def test_find_resume_candidates_filters_complete_corrupt_and_mismatched(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    sql = tmp_path / "target.sql"
    sql.write_text("select 1", encoding="utf-8")
    other = tmp_path / "other.sql"
    other.write_text("select 2", encoding="utf-8")

    good = runs / "a"
    good.mkdir(parents=True)
    (good / "progress.json").write_text(
        json.dumps(
            {
                "complete": False,
                "source_path": str(sql.resolve()),
                "source_kind": "file",
                "page_index": 3,
                "last_cursor": "c3",
            }
        ),
        encoding="utf-8",
    )
    done = runs / "b"
    done.mkdir()
    (done / "progress.json").write_text(
        json.dumps(
            {
                "complete": True,
                "source_path": str(sql.resolve()),
                "source_kind": "file",
                "page_index": 9,
                "last_cursor": "done",
            }
        ),
        encoding="utf-8",
    )
    bad = runs / "c"
    bad.mkdir()
    (bad / "progress.json").write_text("{not-json", encoding="utf-8")
    mismatch = runs / "d"
    mismatch.mkdir()
    (mismatch / "progress.json").write_text(
        json.dumps(
            {
                "complete": False,
                "source_path": str(other.resolve()),
                "source_kind": "file",
                "page_index": 1,
                "last_cursor": "x",
            }
        ),
        encoding="utf-8",
    )
    legacy = runs / "e"
    legacy.mkdir()
    (legacy / "progress.json").write_text(
        json.dumps({"complete": False, "page_index": 2, "last_cursor": "legacy"}),
        encoding="utf-8",
    )

    found = run_sql_folder.find_resume_candidates(runs, target=sql)
    assert len(found) == 1
    assert found[0].last_cursor == "c3"
    assert found[0].page_index == 3
    assert found[0].source_kind == "file"


def test_interactive_file_fresh_builds_output_and_calls_paginate(tmp_path: Path, monkeypatch) -> None:
    sql = tmp_path / "demo.sql"
    sql.write_text("SELECT assignment_id FROM t", encoding="utf-8")
    runs = tmp_path / "runs"
    captured: list[object] = []

    def fake_paginate(args):  # type: ignore[no-untyped-def]
        captured.append(args)
        return 0

    monkeypatch.setattr(run_sql_folder, "run_paginate_mode", fake_paginate)
    answers = iter(["1", str(sql), "2", "0"])
    outputs: list[str] = []

    code = run_sql_folder.run_interactive_menu(
        input_fn=lambda _prompt="": next(answers),
        output_fn=lambda *a, **k: outputs.append(" ".join(str(x) for x in a)),
        runs_dir=runs,
    )
    assert code == 0
    assert len(captured) == 1
    args = captured[0]
    assert args.sql_file == str(sql.resolve()) or args.sql_file == str(sql)
    assert args.manual_login is False
    assert args.superset_mode == "ui"
    assert args.cursor_column == "assignment_id"
    assert args.row_cap == 1000
    assert args.sleep == 0.8
    assert Path(args.output_dir).parent == runs
    assert "demo" in Path(args.output_dir).name


def test_interactive_folder_fresh_writes_progress_and_calls_folder(tmp_path: Path, monkeypatch) -> None:
    sql_dir = tmp_path / "batch"
    sql_dir.mkdir()
    (sql_dir / "a.sql").write_text("SELECT 1", encoding="utf-8")
    runs = tmp_path / "runs"
    captured: list[object] = []

    def fake_folder(args):  # type: ignore[no-untyped-def]
        captured.append(args)
        return 0

    monkeypatch.setattr(run_sql_folder, "run_folder_mode", fake_folder)
    answers = iter(["2", str(sql_dir), "2", "0"])

    code = run_sql_folder.run_interactive_menu(
        input_fn=lambda _prompt="": next(answers),
        output_fn=lambda *a, **k: None,
        runs_dir=runs,
    )
    assert code == 0
    assert len(captured) == 1
    args = captured[0]
    assert Path(args.sql_dir) == sql_dir.resolve() or Path(args.sql_dir) == sql_dir
    assert args.manual_login is False
    assert args.superset_mode == "ui"
    assert args.retry == 2
    assert args.no_resume is False
    assert args.sleep == 0.8
    progress = json.loads((Path(args.output_dir) / "progress.json").read_text(encoding="utf-8"))
    assert progress["complete"] is True
    assert progress["source_kind"] == "folder"
    assert Path(progress["source_path"]) == sql_dir.resolve()


def test_interactive_file_resume_reuses_output_dir(tmp_path: Path, monkeypatch) -> None:
    sql = tmp_path / "demo.sql"
    sql.write_text("SELECT assignment_id FROM t", encoding="utf-8")
    runs = tmp_path / "runs"
    prior = runs / "prior_run"
    prior.mkdir(parents=True)
    (prior / "progress.json").write_text(
        json.dumps(
            {
                "complete": False,
                "source_path": str(sql.resolve()),
                "source_kind": "file",
                "page_index": 4,
                "last_cursor": "cursor-4",
            }
        ),
        encoding="utf-8",
    )
    captured: list[object] = []

    def fake_paginate(args):  # type: ignore[no-untyped-def]
        captured.append(args)
        return 0

    monkeypatch.setattr(run_sql_folder, "run_paginate_mode", fake_paginate)
    answers = iter(["1", str(sql), "1", "0"])

    code = run_sql_folder.run_interactive_menu(
        input_fn=lambda _prompt="": next(answers),
        output_fn=lambda *a, **k: None,
        runs_dir=runs,
    )
    assert code == 0
    assert len(captured) == 1
    assert Path(captured[0].output_dir) == prior
    assert captured[0].start_after is None
    assert captured[0].no_resume is False


def test_interactive_invalid_menu_then_exit(tmp_path: Path) -> None:
    answers = iter(["9", "0"])
    outputs: list[str] = []
    code = run_sql_folder.run_interactive_menu(
        input_fn=lambda _prompt="": next(answers),
        output_fn=lambda *a, **k: outputs.append(" ".join(str(x) for x in a)),
        runs_dir=tmp_path / "runs",
    )
    assert code == 0
    assert any("tidak valid" in line.lower() or "pilihan" in line.lower() for line in outputs)


def test_interactive_latest_resume_empty_message(tmp_path: Path) -> None:
    answers = iter(["3", "0"])
    outputs: list[str] = []
    code = run_sql_folder.run_interactive_menu(
        input_fn=lambda _prompt="": next(answers),
        output_fn=lambda *a, **k: outputs.append(" ".join(str(x) for x in a)),
        runs_dir=tmp_path / "runs",
    )
    assert code == 0
    joined = "\n".join(outputs).lower()
    assert "tidak ada" in joined or "belum ada" in joined


def test_main_zero_args_non_tty_returns_2(monkeypatch) -> None:
    monkeypatch.setattr(run_sql_folder.sys, "argv", ["run_sql_folder.py"])
    monkeypatch.setattr(run_sql_folder.sys.stdin, "isatty", lambda: False)
    code = run_sql_folder.main()
    assert code == 2


def test_main_with_args_skips_menu(monkeypatch, tmp_path: Path) -> None:
    sql = tmp_path / "q.sql"
    sql.write_text("SELECT assignment_id FROM t", encoding="utf-8")
    called = SimpleNamespace(menu=False, paginate=False)

    def boom_menu(**_kwargs):  # type: ignore[no-untyped-def]
        called.menu = True
        return 0

    def fake_paginate(args):  # type: ignore[no-untyped-def]
        called.paginate = True
        return 0

    monkeypatch.setattr(run_sql_folder, "run_interactive_menu", boom_menu)
    monkeypatch.setattr(run_sql_folder, "run_paginate_mode", fake_paginate)
    monkeypatch.setattr(
        run_sql_folder.sys,
        "argv",
        [
            "run_sql_folder.py",
            "--sql-file",
            str(sql),
            "--output-dir",
            str(tmp_path / "out"),
            "--no-xlsx",
        ],
    )
    code = run_sql_folder.main()
    assert code == 0
    assert called.menu is False
    assert called.paginate is True

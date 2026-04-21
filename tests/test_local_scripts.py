from pathlib import Path

from app.services.local_scripts import LocalScriptRegistry


def test_registry_rejects_paths_outside_allowlisted_root(tmp_path: Path) -> None:
    registry = LocalScriptRegistry(allowed_roots=[tmp_path / "allowed"])

    outside_script = tmp_path / "outside.py"
    outside_script.write_text("print('hello')", encoding="utf-8")

    try:
        registry.register_script("outside", outside_script)
    except ValueError as exc:
        assert "allowlisted" in str(exc)
    else:
        raise AssertionError("Expected outside script to be rejected")


def test_registry_runs_registered_script_and_captures_stdout(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True)
    script_path = allowed_root / "hello.py"
    script_path.write_text("print('hello from script')", encoding="utf-8")

    registry = LocalScriptRegistry(allowed_roots=[allowed_root])
    registry.register_script("hello", script_path)

    result = registry.run_script("hello")

    assert result.returncode == 0
    assert "hello from script" in result.stdout

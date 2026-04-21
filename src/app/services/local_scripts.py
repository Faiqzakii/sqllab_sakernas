from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LocalScriptResult:
    returncode: int
    stdout: str
    stderr: str


class LocalScriptRegistry:
    def __init__(self, allowed_roots: list[Path]) -> None:
        self.allowed_roots = [root.resolve() for root in allowed_roots]
        self._scripts: dict[str, Path] = {}

    def register_script(self, name: str, path: Path) -> None:
        resolved_path = path.resolve()
        if not any(root == resolved_path or root in resolved_path.parents for root in self.allowed_roots):
            raise ValueError("Script path must live under an allowlisted root")
        self._scripts[name] = resolved_path

    def run_script(self, name: str) -> LocalScriptResult:
        script_path = self._scripts[name]
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return LocalScriptResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

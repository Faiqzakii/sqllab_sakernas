from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
STATIC_DIR = BASE_DIR / "src" / "app" / "static"
STATIC_STYLES_DIR = STATIC_DIR / "styles"
STATIC_SRC_DIR = BASE_DIR / "src" / "app" / "static_src"
SQLITE_PATH = DATA_DIR / "platform.db"

from __future__ import annotations

import pytest
from sqlmodel import SQLModel, create_engine

import app.api.dashboard as dashboard_api_module
import app.api.findings as findings_api_module
import app.api.resources as resources_api_module
import app.api.runs as runs_api_module
import app.db as db_module
import app.web as web_module


@pytest.fixture(autouse=True)
def isolated_test_database(tmp_path, monkeypatch):
    database_path = tmp_path / "test-platform.db"
    engine = create_engine(f"sqlite:///{database_path}", echo=False)
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(resources_api_module, "engine", engine)
    monkeypatch.setattr(runs_api_module, "engine", engine)
    monkeypatch.setattr(findings_api_module, "engine", engine)
    monkeypatch.setattr(dashboard_api_module, "engine", engine)
    monkeypatch.setattr(web_module, "engine", engine)

    yield

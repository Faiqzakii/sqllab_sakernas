from __future__ import annotations

from sqlmodel import SQLModel, create_engine

from app.config import DATA_DIR, SQLITE_PATH

# Import models before create_all so SQLModel metadata is populated.
from app import models  # noqa: F401


DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{SQLITE_PATH}", echo=False)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


create_db_and_tables()

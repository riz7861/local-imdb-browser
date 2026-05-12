from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "imdb.db"
DATABASE_PATH_ENV = "DATABASE_PATH"
LEGACY_DATABASE_PATH_ENV = "IMDB_BROWSER_DB"


def configured_database_path(value: str | Path | None = None) -> Path:
    raw_value = value or os.environ.get(DATABASE_PATH_ENV) or os.environ.get(
        LEGACY_DATABASE_PATH_ENV
    )
    return Path(raw_value or DEFAULT_DB_PATH).expanduser().resolve()

from __future__ import annotations

import os
import gzip
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "imdb.db"
DATABASE_PATH_ENV = "DATABASE_PATH"
DATABASE_DOWNLOAD_URL_ENV = "DATABASE_DOWNLOAD_URL"
LEGACY_DATABASE_PATH_ENV = "IMDB_BROWSER_DB"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_PROGRESS_BYTES = 64 * 1024 * 1024


class LoggerLike(Protocol):
    def info(self, message: str, *args: object) -> None: ...

    def error(self, message: str, *args: object) -> None: ...


class DatabaseBootstrapError(RuntimeError):
    pass


def configured_database_path(value: str | Path | None = None) -> Path:
    raw_value = value or os.environ.get(DATABASE_PATH_ENV) or os.environ.get(
        LEGACY_DATABASE_PATH_ENV
    )
    return Path(raw_value or DEFAULT_DB_PATH).expanduser().resolve()


def configured_database_download_url(value: str | None = None) -> str:
    return (value or os.environ.get(DATABASE_DOWNLOAD_URL_ENV) or "").strip()


def bootstrap_database_from_download_url(
    db_path: Path,
    download_url: str | None = None,
    logger: LoggerLike | None = None,
) -> None:
    if db_path.exists():
        _log_info(logger, "Database already exists at %s; skipping bootstrap download.", db_path)
        return

    url = configured_database_download_url(download_url)
    if not url:
        _log_info(
            logger,
            "Database not found at %s and DATABASE_DOWNLOAD_URL is not set; skipping bootstrap download.",
            db_path,
        )
        return

    tmp_path = db_path.with_name(f".{db_path.name}.download")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.unlink(missing_ok=True)

    try:
        _log_info(
            logger,
            "Database not found at %s; downloading bootstrap database from DATABASE_DOWNLOAD_URL.",
            db_path,
        )
        with urlopen(url, timeout=60) as response, tmp_path.open("wb") as output:
            gzip_download = _is_gzip_download(url, response.headers)
            if gzip_download:
                _log_info(logger, "Database bootstrap detected gzip-compressed input.")
            stream = gzip.GzipFile(fileobj=response) if gzip_download else response
            content_length = (
                None
                if gzip_download
                else _content_length(response.headers.get("Content-Length"))
            )
            _stream_response(stream, output, content_length, logger)
        tmp_path.replace(db_path)
        _log_info(logger, "Database bootstrap complete: %s", db_path)
    except (OSError, HTTPError, URLError, TimeoutError) as exc:
        tmp_path.unlink(missing_ok=True)
        message = (
            f"Could not download database from DATABASE_DOWNLOAD_URL to {db_path}: {exc}"
        )
        if logger:
            logger.error("%s", message)
        raise DatabaseBootstrapError(message) from exc


def _stream_response(
    response: object,
    output: object,
    content_length: int | None,
    logger: LoggerLike | None,
) -> None:
    downloaded = 0
    next_progress = DOWNLOAD_PROGRESS_BYTES
    while True:
        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
        if not chunk:
            break
        output.write(chunk)
        downloaded += len(chunk)
        if downloaded >= next_progress:
            _log_download_progress(downloaded, content_length, logger)
            next_progress += DOWNLOAD_PROGRESS_BYTES
    _log_download_progress(downloaded, content_length, logger)


def _content_length(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _is_gzip_download(url: str, headers: object) -> bool:
    clean_url = url.split("?", 1)[0].lower()
    if clean_url.endswith(".gz"):
        return True
    content_encoding = (headers.get("Content-Encoding") or "").lower()
    content_type = (headers.get("Content-Type") or "").lower()
    return "gzip" in content_encoding or "gzip" in content_type


def _log_download_progress(
    downloaded: int, content_length: int | None, logger: LoggerLike | None
) -> None:
    downloaded_mb = downloaded / (1024 * 1024)
    if content_length:
        total_mb = content_length / (1024 * 1024)
        _log_info(
            logger,
            "Database bootstrap wrote %.1f MB of %.1f MB.",
            downloaded_mb,
            total_mb,
        )
    else:
        _log_info(logger, "Database bootstrap wrote %.1f MB.", downloaded_mb)


def _log_info(logger: LoggerLike | None, message: str, *args: object) -> None:
    if logger:
        logger.info(message, *args)

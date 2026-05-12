from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from db_paths import BASE_DIR, configured_database_path

TMDB_FIND_URL = "https://api.themoviedb.org/3/find/{title_id}"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
DEFAULT_LIMIT = 100
DEFAULT_TYPES = ["movie", "tvSeries", "tvMiniSeries", "tvMovie"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch TMDb poster URLs by IMDb ID.")
    parser.add_argument(
        "--db",
        help="Path to imdb.db; defaults to DATABASE_PATH or local imdb.db",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Maximum titles to fetch")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between requests")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    api_key = os.environ.get("TMDB_API_KEY")
    if not api_key:
        raise SystemExit("TMDB_API_KEY must be set in .env or the environment.")

    with sqlite3.connect(configured_database_path(args.db)) as conn:
        ensure_poster_schema(conn)
        title_ids = candidate_title_ids(conn, max(args.limit, 0))
        fetched = 0
        skipped = 0
        print(f"Fetching posters for {len(title_ids)} title(s)")
        for index, title_id in enumerate(title_ids, start=1):
            try:
                payload = fetch_tmdb(title_id, api_key)
                poster = parse_tmdb_payload(payload)
                upsert_poster(conn, title_id, poster)
                conn.commit()
                fetched += 1
                print(f"[{index}] {title_id} fetched={fetched} skipped={skipped}")
            except (HTTPError, URLError, TimeoutError) as exc:
                skipped += 1
                print(f"[{index}] {title_id} failed: {exc}")
            if index < len(title_ids):
                time.sleep(max(args.delay, 0))
    return 0


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def ensure_poster_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER NOT NULL DEFAULT 1,
            title_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'plan_to_watch',
            notes TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, title_id)
        )
        """
    )
    migrate_watchlist_schema(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_title_id ON watchlist(title_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS poster_cache (
            title_id TEXT PRIMARY KEY,
            poster_url TEXT,
            tmdb_id INTEGER,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_poster_cache_title ON poster_cache(title_id)")


def migrate_watchlist_schema(conn: sqlite3.Connection) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(watchlist)")]
    if "user_id" in columns:
        return

    conn.execute("ALTER TABLE watchlist RENAME TO watchlist_legacy")
    conn.execute(
        """
        CREATE TABLE watchlist (
            user_id INTEGER NOT NULL DEFAULT 1,
            title_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'plan_to_watch',
            notes TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, title_id)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO watchlist (user_id, title_id, status, notes, added_at)
        SELECT 1, title_id, status, notes, added_at
        FROM watchlist_legacy
        """
    )
    conn.execute("DROP TABLE watchlist_legacy")


def candidate_title_ids(conn: sqlite3.Connection, limit: int) -> list[str]:
    placeholders = ", ".join("?" for _ in DEFAULT_TYPES)
    sql = f"""
        SELECT t.title_id
        FROM titles AS t
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN watchlist AS wl ON wl.title_id = t.title_id
        LEFT JOIN poster_cache AS pc ON pc.title_id = t.title_id
        WHERE pc.title_id IS NULL
          AND COALESCE(t.is_adult, 0) = 0
          AND t.type IN ({placeholders})
        GROUP BY t.title_id
        ORDER BY
            CASE WHEN MAX(wl.title_id IS NOT NULL) THEN 0 ELSE 1 END,
            COALESCE(r.votes, 0) DESC,
            t.primary_title COLLATE NOCASE ASC
        LIMIT ?
    """
    rows = conn.execute(sql, (*DEFAULT_TYPES, limit)).fetchall()
    return [row[0] for row in rows]


def fetch_tmdb(title_id: str, api_key: str) -> dict[str, object]:
    query = urlencode({"api_key": api_key, "external_source": "imdb_id"})
    with urlopen(f"{TMDB_FIND_URL.format(title_id=title_id)}?{query}", timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_tmdb_payload(payload: dict[str, object]) -> dict[str, object | None]:
    for key in ("movie_results", "tv_results"):
        results = payload.get(key)
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                poster_path = first.get("poster_path")
                return {
                    "tmdb_id": first.get("id") if isinstance(first.get("id"), int) else None,
                    "poster_url": f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None,
                }
    return {"tmdb_id": None, "poster_url": None}


def upsert_poster(
    conn: sqlite3.Connection, title_id: str, poster: dict[str, object | None]
) -> None:
    conn.execute(
        """
        INSERT INTO poster_cache (title_id, poster_url, tmdb_id, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(title_id) DO UPDATE SET
            poster_url = excluded.poster_url,
            tmdb_id = excluded.tmdb_id,
            fetched_at = excluded.fetched_at
        """,
        (
            title_id,
            poster["poster_url"],
            poster["tmdb_id"],
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())

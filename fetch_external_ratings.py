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


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "imdb.db"
OMDB_URL = "https://www.omdbapi.com/"
DEFAULT_TYPES = ["movie", "tvSeries", "tvMiniSeries", "tvMovie"]
DEFAULT_MIN_VOTES = 5000


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Metascore and Rotten Tomatoes ratings from OMDb."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to imdb.db")
    parser.add_argument("--limit", type=int, default=100, help="Maximum titles to fetch")
    parser.add_argument(
        "--min-votes",
        type=int,
        default=DEFAULT_MIN_VOTES,
        help="Minimum IMDb vote count for non-watchlist titles",
    )
    parser.add_argument(
        "--types",
        default=",".join(DEFAULT_TYPES),
        help="Comma-separated IMDb title types to fetch",
    )
    parser.add_argument(
        "--watchlist-priority",
        action="store_true",
        help="Also fetch watchlist titles below --min-votes and process them first",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh rows even when external ratings were already fetched",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Seconds to wait between OMDb requests",
    )
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    api_key = os.environ.get("OMDB_API_KEY")
    if not api_key:
        raise SystemExit("OMDB_API_KEY must be set in .env or the environment.")

    db_path = Path(args.db).expanduser().resolve()
    title_types = parse_types(args.types)
    with sqlite3.connect(db_path) as conn:
        ensure_external_schema(conn)
        plan = build_fetch_plan(
            conn,
            limit=args.limit,
            force=args.force,
            min_votes=args.min_votes,
            title_types=title_types,
            watchlist_priority=args.watchlist_priority,
        )
        print_progress_header(plan)
        fetched = 0
        failed = 0
        for index, title_id in enumerate(plan.title_ids, start=1):
            try:
                payload = fetch_omdb(title_id, api_key)
                ratings = parse_omdb_payload(payload)
                upsert_external_rating(conn, title_id, ratings)
                conn.commit()
                fetched += 1
                print_progress_row(
                    index,
                    title_id,
                    fetched,
                    plan.skipped,
                    plan.remaining_before_limit - fetched,
                )
            except (HTTPError, URLError, TimeoutError) as exc:
                failed += 1
                print(
                    f"[{index}/{plan.to_fetch}] {title_id} failed: {exc} "
                    f"fetched={fetched} skipped={plan.skipped} "
                    f"remaining={plan.remaining_before_limit - fetched} "
                    f"api_quota_estimate={index} used"
                )
            if index < plan.to_fetch:
                time.sleep(max(args.delay, 0))
        print(
            "Done: "
            f"fetched={fetched}, failed={failed}, skipped={plan.skipped}, "
            f"remaining={max(plan.remaining_before_limit - fetched, 0)}, "
            f"api_quota_estimate={plan.to_fetch} request(s)"
        )

    return 0


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def ensure_external_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_title_id ON watchlist(title_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS external_ratings (
            title_id TEXT PRIMARY KEY,
            metascore INTEGER,
            rotten_tomatoes_score INTEGER,
            omdb_imdb_rating REAL,
            fetched_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_metascore ON external_ratings(metascore)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_external_rotten_tomatoes
        ON external_ratings(rotten_tomatoes_score)
        """
    )


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


class FetchPlan:
    def __init__(
        self,
        title_ids: list[str],
        eligible: int,
        skipped: int,
        remaining_before_limit: int,
    ) -> None:
        self.title_ids = title_ids
        self.eligible = eligible
        self.skipped = skipped
        self.remaining_before_limit = remaining_before_limit

    @property
    def to_fetch(self) -> int:
        return len(self.title_ids)

    @property
    def remaining_after_run(self) -> int:
        return max(self.remaining_before_limit - self.to_fetch, 0)


def build_fetch_plan(
    conn: sqlite3.Connection,
    limit: int,
    force: bool,
    min_votes: int,
    title_types: list[str],
    watchlist_priority: bool,
) -> FetchPlan:
    eligible_where, eligible_params = eligibility_sql(
        min_votes=min_votes,
        title_types=title_types,
        watchlist_priority=watchlist_priority,
    )
    eligible = count_titles(conn, eligible_where, eligible_params)

    if force:
        skipped = 0
        remaining_where = eligible_where
        remaining_params = eligible_params
    else:
        skipped_where = eligible_where + " AND er.title_id IS NOT NULL"
        skipped = count_titles(conn, skipped_where, eligible_params)
        remaining_where = eligible_where + " AND er.title_id IS NULL"
        remaining_params = eligible_params

    remaining_before_limit = count_titles(conn, remaining_where, remaining_params)
    title_ids = candidate_title_ids(
        conn,
        where_sql=remaining_where,
        params=remaining_params,
        limit=limit,
        watchlist_priority=watchlist_priority,
    )
    return FetchPlan(
        title_ids=title_ids,
        eligible=eligible,
        skipped=skipped,
        remaining_before_limit=remaining_before_limit,
    )


def eligibility_sql(
    min_votes: int, title_types: list[str], watchlist_priority: bool
) -> tuple[str, list[object]]:
    if not title_types:
        title_types = DEFAULT_TYPES.copy()
    placeholders = ", ".join("?" for _ in title_types)
    where = [
        "COALESCE(t.is_adult, 0) = 0",
        f"t.type IN ({placeholders})",
    ]
    params: list[object] = [*title_types]
    if watchlist_priority:
        where.append("(COALESCE(r.votes, 0) >= ? OR wl.title_id IS NOT NULL)")
    else:
        where.append("COALESCE(r.votes, 0) >= ?")
    params.append(max(min_votes, 0))
    return " AND ".join(where), params


def count_titles(conn: sqlite3.Connection, where_sql: str, params: list[object]) -> int:
    sql = f"""
        SELECT COUNT(*)
        FROM titles AS t
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN (SELECT DISTINCT title_id FROM watchlist) AS wl
            ON wl.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        WHERE {where_sql}
    """
    return int(conn.execute(sql, params).fetchone()[0])


def candidate_title_ids(
    conn: sqlite3.Connection,
    where_sql: str,
    params: list[object],
    limit: int,
    watchlist_priority: bool,
) -> list[str]:
    watchlist_order = "CASE WHEN wl.title_id IS NOT NULL THEN 0 ELSE 1 END, " if watchlist_priority else ""
    sql = f"""
        SELECT t.title_id
        FROM titles AS t
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN (SELECT DISTINCT title_id FROM watchlist) AS wl
            ON wl.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        WHERE {where_sql}
        ORDER BY {watchlist_order}COALESCE(r.votes, 0) DESC, t.primary_title COLLATE NOCASE ASC
        LIMIT ?
    """
    rows = conn.execute(sql, (*params, max(limit, 0))).fetchall()
    return [row[0] for row in rows]


def parse_types(value: str) -> list[str]:
    title_types = [item.strip() for item in value.split(",") if item.strip()]
    return title_types or DEFAULT_TYPES.copy()


def print_progress_header(plan: FetchPlan) -> None:
    print(
        "Plan: "
        f"eligible={plan.eligible}, skipped={plan.skipped}, "
        f"remaining={plan.remaining_before_limit}, "
        f"api_quota_estimate={plan.to_fetch} request(s)"
    )
    print(f"Fetching {plan.to_fetch} title(s)")


def print_progress_row(
    index: int, title_id: str, fetched: int, skipped: int, remaining: int
) -> None:
    print(
        f"[{index}] {title_id} "
        f"fetched={fetched} skipped={skipped} remaining={max(remaining, 0)} "
        f"api_quota_estimate={fetched} used"
    )


def fetch_omdb(title_id: str, api_key: str) -> dict[str, object]:
    query = urlencode({"i": title_id, "apikey": api_key})
    with urlopen(f"{OMDB_URL}?{query}", timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_omdb_payload(payload: dict[str, object]) -> dict[str, int | float | None]:
    ratings = payload.get("Ratings") or []
    rotten_tomatoes = None
    if isinstance(ratings, list):
        for item in ratings:
            if not isinstance(item, dict):
                continue
            if item.get("Source") == "Rotten Tomatoes":
                rotten_tomatoes = parse_percent(item.get("Value"))
                break

    return {
        "metascore": parse_int(payload.get("Metascore")),
        "rotten_tomatoes_score": rotten_tomatoes,
        "omdb_imdb_rating": parse_float(payload.get("imdbRating")),
    }


def parse_int(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def parse_percent(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    text = str(value).strip()
    if not text.endswith("%"):
        return None
    return parse_int(text[:-1])


def parse_float(value: object) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def upsert_external_rating(
    conn: sqlite3.Connection, title_id: str, ratings: dict[str, int | float | None]
) -> None:
    conn.execute(
        """
        INSERT INTO external_ratings (
            title_id,
            metascore,
            rotten_tomatoes_score,
            omdb_imdb_rating,
            fetched_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(title_id) DO UPDATE SET
            metascore = excluded.metascore,
            rotten_tomatoes_score = excluded.rotten_tomatoes_score,
            omdb_imdb_rating = excluded.omdb_imdb_rating,
            fetched_at = excluded.fetched_at
        """,
        (
            title_id,
            ratings["metascore"],
            ratings["rotten_tomatoes_score"],
            ratings["omdb_imdb_rating"],
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())

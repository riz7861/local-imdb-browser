from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db_paths import BASE_DIR, DEFAULT_DB_PATH


DEFAULT_OUTPUT_PATH = BASE_DIR / "imdb_slim.db"
DEFAULT_MIN_VOTES = 1000
DEFAULT_START_YEAR = 1950
RAILWAY_STORAGE_LIMIT_BYTES = 500 * 1024 * 1024
RAILWAY_COMFORT_BYTES = 450 * 1024 * 1024
HOLLYWOOD_REGIONS = ["US", "GB", "CA", "AU"]
HOLLYWOOD_LANGUAGES = ["en"]
MOVIE_TYPES = ["movie"]
TV_TYPES = ["tvSeries", "tvMiniSeries", "tvMovie"]


@dataclass(frozen=True)
class SlimBuildStats:
    input_path: Path
    output_path: Path
    original_title_count: int
    slim_title_count: int
    output_size_bytes: int
    akas_used: bool
    approximate_language_filter: bool

    @property
    def railway_status(self) -> str:
        if self.output_size_bytes <= RAILWAY_COMFORT_BYTES:
            return "likely compatible with Railway's 500MB volume target"
        if self.output_size_bytes <= RAILWAY_STORAGE_LIMIT_BYTES:
            return "under 500MB, but close to Railway's volume target"
        return "too large for Railway's 500MB volume target"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a smaller Railway-oriented SQLite database from imdb.db."
    )
    parser.add_argument("--input", default=str(DEFAULT_DB_PATH), help="Source imdb.db path")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output slim DB path; defaults to imdb_slim.db",
    )
    parser.add_argument(
        "--min-votes",
        type=int,
        default=DEFAULT_MIN_VOTES,
        help="Minimum IMDb votes for non-watchlist titles",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=DEFAULT_START_YEAR,
        help="Minimum start/premiere year for non-watchlist titles",
    )
    parser.add_argument(
        "--hollywood-only",
        action="store_true",
        help="Prefer US/GB/CA/AU or English akas; falls back broadly without akas",
    )
    parser.add_argument(
        "--include-tv",
        action="store_true",
        help="Include tvSeries, tvMiniSeries, and tvMovie title types",
    )
    parser.add_argument(
        "--include-watchlist-always",
        action="store_true",
        help="Keep non-adult watchlist titles even when they miss vote/year/language filters",
    )
    args = parser.parse_args()

    stats = build_slim_database(
        Path(args.input).expanduser().resolve(),
        Path(args.output).expanduser().resolve(),
        min_votes=max(args.min_votes, 0),
        start_year=args.start_year,
        hollywood_only=args.hollywood_only,
        include_tv=args.include_tv,
        include_watchlist_always=args.include_watchlist_always,
    )
    print_stats(stats)
    return 0


def build_slim_database(
    input_path: Path,
    output_path: Path,
    *,
    min_votes: int = DEFAULT_MIN_VOTES,
    start_year: int = DEFAULT_START_YEAR,
    hollywood_only: bool = False,
    include_tv: bool = False,
    include_watchlist_always: bool = False,
) -> SlimBuildStats:
    input_path = input_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input database not found: {input_path}")
    if input_path == output_path:
        raise SystemExit("--input and --output must be different paths.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    tmp_path.unlink(missing_ok=True)

    try:
        stats = _build_to_temp_database(
            input_path,
            tmp_path,
            output_path,
            min_votes=min_votes,
            start_year=start_year,
            hollywood_only=hollywood_only,
            include_tv=include_tv,
            include_watchlist_always=include_watchlist_always,
        )
        tmp_path.replace(output_path)
        return SlimBuildStats(
            input_path=stats.input_path,
            output_path=output_path,
            original_title_count=stats.original_title_count,
            slim_title_count=stats.slim_title_count,
            output_size_bytes=output_path.stat().st_size,
            akas_used=stats.akas_used,
            approximate_language_filter=stats.approximate_language_filter,
        )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _build_to_temp_database(
    input_path: Path,
    tmp_path: Path,
    output_path: Path,
    *,
    min_votes: int,
    start_year: int,
    hollywood_only: bool,
    include_tv: bool,
    include_watchlist_always: bool,
) -> SlimBuildStats:
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("ATTACH DATABASE ? AS src", (str(input_path),))
        require_table(conn, "titles")
        require_table(conn, "ratings")

        original_count = scalar_int(conn, "SELECT COUNT(*) FROM src.titles")
        akas_available = table_exists(conn, "src", "akas")
        allowed_types = MOVIE_TYPES + (TV_TYPES if include_tv else [])
        create_core_tables(conn)
        create_app_tables(conn)
        create_selected_title_ids(
            conn,
            allowed_types=allowed_types,
            min_votes=min_votes,
            start_year=start_year,
            hollywood_only=hollywood_only,
            akas_available=akas_available,
            include_watchlist_always=include_watchlist_always,
        )
        copy_selected_table(conn, "titles", "t")
        copy_selected_table(conn, "ratings", "r")
        copy_optional_selected_table(conn, "external_ratings", "er")
        copy_optional_selected_table(conn, "poster_cache", "pc")
        copy_users(conn)
        copy_watchlist(conn)
        create_indexes(conn)
        slim_count = scalar_int(conn, "SELECT COUNT(*) FROM titles")
        conn.commit()
        conn.execute("VACUUM")
        conn.close()
    except Exception:
        conn.close()
        raise

    return SlimBuildStats(
        input_path=input_path,
        output_path=output_path,
        original_title_count=original_count,
        slim_title_count=slim_count,
        output_size_bytes=tmp_path.stat().st_size,
        akas_used=hollywood_only and akas_available,
        approximate_language_filter=hollywood_only and not akas_available,
    )


def create_core_tables(conn: sqlite3.Connection) -> None:
    for table in ("titles", "ratings"):
        sql = conn.execute(
            "SELECT sql FROM src.sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not sql or not sql[0]:
            raise SystemExit(f"Source database is missing required table: {table}")
        conn.execute(sql[0])


def create_app_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_ratings (
            title_id TEXT PRIMARY KEY,
            metascore INTEGER,
            rotten_tomatoes_score INTEGER,
            omdb_imdb_rating REAL,
            fetched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS poster_cache (
            title_id TEXT PRIMARY KEY,
            poster_url TEXT,
            tmdb_id INTEGER,
            fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER NOT NULL DEFAULT 1,
            title_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'plan_to_watch',
            notes TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            PRIMARY KEY (user_id, title_id)
        );
        """
    )


def create_selected_title_ids(
    conn: sqlite3.Connection,
    *,
    allowed_types: list[str],
    min_votes: int,
    start_year: int,
    hollywood_only: bool,
    akas_available: bool,
    include_watchlist_always: bool,
) -> None:
    conn.execute("CREATE TEMP TABLE selected_title_ids (title_id TEXT PRIMARY KEY)")
    where = [
        "COALESCE(t.is_adult, 0) = 0",
        in_clause("t.type", allowed_types),
        "r.votes >= ?",
        "t.premiered >= ?",
    ]
    params: list[Any] = [*allowed_types, min_votes, start_year]

    if hollywood_only and akas_available:
        where.append(
            f"""
            EXISTS (
                SELECT 1
                FROM src.akas AS a
                WHERE a.title_id = t.title_id
                  AND (
                    {in_clause("a.region", HOLLYWOOD_REGIONS)}
                    OR {in_clause("a.language", HOLLYWOOD_LANGUAGES)}
                  )
            )
            """
        )
        params.extend([*HOLLYWOOD_REGIONS, *HOLLYWOOD_LANGUAGES])
    elif hollywood_only:
        where.append("t.genres IS NOT NULL")
        where.append("TRIM(t.genres) <> ''")

    conn.execute(
        f"""
        INSERT OR IGNORE INTO selected_title_ids (title_id)
        SELECT t.title_id
        FROM src.titles AS t
        INNER JOIN src.ratings AS r ON r.title_id = t.title_id
        WHERE {" AND ".join(where)}
        """,
        params,
    )

    if include_watchlist_always:
        add_watchlist_title_ids(conn, allowed_types)


def add_watchlist_title_ids(conn: sqlite3.Connection, allowed_types: list[str]) -> None:
    if not table_exists(conn, "src", "watchlist"):
        return
    if "title_id" not in table_columns(conn, "src", "watchlist"):
        return
    conn.execute(
        f"""
        INSERT OR IGNORE INTO selected_title_ids (title_id)
        SELECT DISTINCT w.title_id
        FROM src.watchlist AS w
        INNER JOIN src.titles AS t ON t.title_id = w.title_id
        WHERE COALESCE(t.is_adult, 0) = 0
          AND {in_clause("t.type", allowed_types)}
        """,
        allowed_types,
    )


def copy_selected_table(conn: sqlite3.Connection, table: str, alias: str) -> None:
    source_columns = table_columns(conn, "src", table)
    dest_columns = table_columns(conn, "main", table)
    columns = [column for column in dest_columns if column in source_columns]
    if "title_id" not in columns:
        raise SystemExit(f"{table} must contain a title_id column.")
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    select_sql = ", ".join(f"{alias}.{quote_identifier(column)}" for column in columns)
    conn.execute(
        f"""
        INSERT INTO {quote_identifier(table)} ({column_sql})
        SELECT {select_sql}
        FROM src.{quote_identifier(table)} AS {alias}
        INNER JOIN selected_title_ids AS s ON s.title_id = {alias}.title_id
        """
    )


def copy_optional_selected_table(
    conn: sqlite3.Connection, table: str, alias: str
) -> None:
    if table_exists(conn, "src", table):
        copy_selected_table(conn, table, alias)


def copy_users(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "src", "users"):
        return
    source_columns = table_columns(conn, "src", "users")
    dest_columns = table_columns(conn, "main", "users")
    columns = [column for column in dest_columns if column in source_columns]
    if not columns:
        return
    column_sql = ", ".join(quote_identifier(column) for column in columns)
    select_sql = ", ".join(f"u.{quote_identifier(column)}" for column in columns)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO users ({column_sql})
        SELECT {select_sql}
        FROM src.users AS u
        """
    )


def copy_watchlist(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "src", "watchlist"):
        return
    source_columns = table_columns(conn, "src", "watchlist")
    if "title_id" not in source_columns:
        return

    user_id_expr = "w.user_id" if "user_id" in source_columns else "1"
    status_expr = "w.status" if "status" in source_columns else "'plan_to_watch'"
    notes_expr = "w.notes" if "notes" in source_columns else "''"
    added_at_expr = (
        "w.added_at" if "added_at" in source_columns else "datetime('now')"
    )
    conn.execute(
        f"""
        INSERT OR IGNORE INTO watchlist (user_id, title_id, status, notes, added_at)
        SELECT {user_id_expr}, w.title_id, {status_expr}, {notes_expr}, {added_at_expr}
        FROM src.watchlist AS w
        INNER JOIN selected_title_ids AS s ON s.title_id = w.title_id
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    index_specs = [
        ("idx_slim_titles_type", "titles", ["type"]),
        ("idx_slim_titles_premiered", "titles", ["premiered"]),
        ("idx_slim_titles_primary_title", "titles", ["primary_title"]),
        ("idx_slim_titles_original_title", "titles", ["original_title"]),
        ("idx_slim_titles_type_year", "titles", ["type", "premiered"]),
        ("idx_slim_titles_is_adult", "titles", ["is_adult"]),
        ("idx_slim_ratings_votes", "ratings", ["votes"]),
        ("idx_slim_ratings_rating", "ratings", ["rating"]),
        ("idx_slim_ratings_rating_votes", "ratings", ["rating", "votes"]),
        ("idx_slim_external_metascore", "external_ratings", ["metascore"]),
        (
            "idx_slim_external_rotten_tomatoes",
            "external_ratings",
            ["rotten_tomatoes_score"],
        ),
        (
            "idx_slim_external_metascore_title",
            "external_ratings",
            ["metascore", "title_id"],
        ),
        (
            "idx_slim_external_rt_title",
            "external_ratings",
            ["rotten_tomatoes_score", "title_id"],
        ),
        ("idx_slim_poster_cache_title", "poster_cache", ["title_id"]),
        ("idx_slim_watchlist_user_status", "watchlist", ["user_id", "status"]),
        ("idx_slim_watchlist_user_added_at", "watchlist", ["user_id", "added_at"]),
        ("idx_slim_watchlist_title_id", "watchlist", ["title_id"]),
    ]
    for name, table, columns in index_specs:
        if all(column in table_columns(conn, "main", table) for column in columns):
            column_sql = ", ".join(quote_identifier(column) for column in columns)
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {quote_identifier(name)}
                ON {quote_identifier(table)} ({column_sql})
                """
            )


def print_stats(stats: SlimBuildStats) -> None:
    size_mb = stats.output_size_bytes / (1024 * 1024)
    print(f"Input DB: {stats.input_path}")
    print(f"Output DB: {stats.output_path}")
    print(f"Original title count: {stats.original_title_count:,}")
    print(f"Slim title count: {stats.slim_title_count:,}")
    print(f"Final DB file size: {size_mb:.1f} MB")
    print(f"Estimated Railway compatibility: {stats.railway_status}")
    if stats.akas_used:
        print("Hollywood filter: used akas region/language data.")
    elif stats.approximate_language_filter:
        print(
            "Hollywood filter: akas table missing; used broad vote/type/year/genre filtering."
        )


def require_table(conn: sqlite3.Connection, table: str) -> None:
    if not table_exists(conn, "src", table):
        raise SystemExit(f"Source database is missing required table: {table}")


def table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    return (
        conn.execute(
            f"SELECT 1 FROM {quote_identifier(schema)}.sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def table_columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    rows = conn.execute(
        f"PRAGMA {quote_identifier(schema)}.table_info({quote_identifier(table)})"
    ).fetchall()
    return [row[1] for row in rows]


def scalar_int(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def in_clause(column: str, values: list[str]) -> str:
    if not values:
        return "0 = 1"
    placeholders = ", ".join("?" for _ in values)
    return f"{column} IN ({placeholders})"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


if __name__ == "__main__":
    raise SystemExit(main())

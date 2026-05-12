from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "imdb.db"
DEFAULT_CACHE_DIR = BASE_DIR / "downloads"
ONLY_TABLES = "titles,ratings,episodes"
ONLY_TABLES_WITH_AKAS = "titles,akas,ratings,episodes"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install dependencies and build a local IMDb SQLite database."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to imdb.db")
    parser.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help="Directory for downloaded IMDb TSV files",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Do not install Python dependencies before importing IMDb data",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Do not run imdb-sqlite; only create local app tables and indexes",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete the existing database before importing",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Pass --no-index to imdb-sqlite to save disk space",
    )
    parser.add_argument(
        "--with-akas",
        action="store_true",
        help="Also import title.akas for region/language filters",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    if not args.skip_install:
        install_dependencies()

    if args.rebuild and db_path.exists():
        print(f"Removing existing database: {db_path}")
        db_path.unlink()

    if not args.skip_import:
        if db_path.exists():
            print(f"Using existing database: {db_path}")
        else:
            build_imdb_database(
                db_path,
                cache_dir,
                no_index=args.no_index,
                with_akas=args.with_akas,
            )

    ensure_local_schema(db_path)
    print(f"Ready: {db_path}")
    return 0


def install_dependencies() -> None:
    requirements = BASE_DIR / "requirements.txt"
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run([sys.executable, "-m", "pip", "install", "-r", str(requirements)])


def build_imdb_database(
    db_path: Path, cache_dir: Path, no_index: bool, with_akas: bool
) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    command = resolve_imdb_sqlite_command()
    tables = ONLY_TABLES_WITH_AKAS if with_akas else ONLY_TABLES
    cmd = [
        *command,
        "--db",
        str(db_path),
        "--cache-dir",
        str(cache_dir),
        "--only",
        tables,
    ]
    if no_index:
        cmd.append("--no-index")

    if with_akas:
        print("Building IMDb database with titles, akas, ratings, and episodes.")
    else:
        print("Building IMDb database with titles, ratings, and episodes only.")
    print("This downloads IMDb non-commercial datasets and may take a while.")
    run(cmd)


def resolve_imdb_sqlite_command() -> list[str]:
    executable = shutil.which("imdb-sqlite")
    if executable:
        return [executable]

    executable_dir = Path(sys.executable).parent
    scripts_dir = Path(sysconfig.get_path("scripts"))
    candidates = [
        executable_dir / "imdb-sqlite",
        executable_dir / "imdb-sqlite.exe",
        executable_dir / "Scripts" / "imdb-sqlite",
        executable_dir / "Scripts" / "imdb-sqlite.exe",
        scripts_dir / "imdb-sqlite",
        scripts_dir / "imdb-sqlite.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]

    return [sys.executable, "-m", "imdb_sqlite"]


def ensure_local_schema(db_path: Path) -> None:
    if not db_path.exists():
        raise SystemExit(
            f"{db_path} does not exist. Run without --skip-import to build it first."
        )

    with sqlite3.connect(db_path) as conn:
        has_titles = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'titles'"
        ).fetchone()
        if not has_titles:
            raise SystemExit(f"{db_path} is not an imdb-sqlite database.")

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
            "CREATE INDEX IF NOT EXISTS idx_watchlist_user_status ON watchlist(user_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_user_added_at ON watchlist(user_id, added_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_title_id ON watchlist(title_id)")
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
            """
            CREATE INDEX IF NOT EXISTS idx_external_metascore
            ON external_ratings(metascore)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_external_rotten_tomatoes
            ON external_ratings(rotten_tomatoes_score)
            """
        )
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_titles_type ON titles(type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_titles_primary_title ON titles(primary_title)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_titles_premiered ON titles(premiered)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_titles_type_year ON titles(type, premiered)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_titles_is_adult ON titles(is_adult)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_ratings_rating ON ratings(rating)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_ratings_votes ON ratings(votes)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_ratings_rating_votes ON ratings(rating, votes)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_external_metascore_title ON external_ratings(metascore, title_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_external_rt_title ON external_ratings(rotten_tomatoes_score, title_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_poster_cache_title ON poster_cache(title_id)"
        )
        has_akas = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'akas'"
        ).fetchone()
        if has_akas:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_local_akas_title_id ON akas(title_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_local_akas_region ON akas(region)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_local_akas_language ON akas(language)"
            )
        conn.commit()


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


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.check_call(command)


if __name__ == "__main__":
    raise SystemExit(main())

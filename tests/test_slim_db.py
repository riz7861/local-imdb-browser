from __future__ import annotations

import sqlite3
from pathlib import Path

from build_slim_db import build_slim_database


def make_slim_source_db(path: Path, with_akas: bool) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE titles (
              title_id VARCHAR PRIMARY KEY,
              type VARCHAR,
              primary_title VARCHAR,
              original_title VARCHAR,
              is_adult INTEGER,
              premiered INTEGER,
              ended INTEGER,
              runtime_minutes INTEGER,
              genres VARCHAR
            );
            CREATE TABLE ratings (
              title_id VARCHAR PRIMARY KEY,
              rating REAL,
              votes INTEGER
            );
            CREATE TABLE episodes (
              episode_title_id VARCHAR,
              show_title_id VARCHAR,
              season_number INTEGER,
              episode_number INTEGER
            );
            CREATE TABLE external_ratings (
              title_id TEXT PRIMARY KEY,
              metascore INTEGER,
              rotten_tomatoes_score INTEGER,
              omdb_imdb_rating REAL,
              fetched_at TEXT
            );
            CREATE TABLE poster_cache (
              title_id TEXT PRIMARY KEY,
              poster_url TEXT,
              tmdb_id INTEGER,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE watchlist (
              user_id INTEGER NOT NULL DEFAULT 1,
              title_id TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'plan_to_watch',
              notes TEXT NOT NULL DEFAULT '',
              added_at TEXT NOT NULL,
              PRIMARY KEY (user_id, title_id)
            );
            """
        )
        if with_akas:
            conn.executescript(
                """
                CREATE TABLE akas (
                  title_id VARCHAR,
                  title VARCHAR,
                  region VARCHAR,
                  language VARCHAR,
                  types VARCHAR,
                  attributes VARCHAR,
                  is_original_title INTEGER
                );
                """
            )

        titles = [
            ("ttkeepmovie", "movie", "Keep Movie", "Keep Movie", 0, 2001, None, 100, "Drama"),
            ("ttlowvote", "movie", "Low Vote", "Low Vote", 0, 2002, None, 100, "Drama"),
            ("ttadult", "movie", "Adult Movie", "Adult Movie", 1, 2003, None, 100, "Drama"),
            ("ttepisode", "tvEpisode", "Episode", "Episode", 0, 2004, None, 45, "Drama"),
            ("ttseries", "tvSeries", "Keep Series", "Keep Series", 0, 2005, None, 45, "Drama"),
            ("ttmini", "tvMiniSeries", "Keep Mini", "Keep Mini", 0, 2006, None, 45, "Drama"),
            ("tttvmovie", "tvMovie", "Keep TV Movie", "Keep TV Movie", 0, 2007, None, 90, "Drama"),
            ("ttshort", "short", "Short", "Short", 0, 2008, None, 12, "Short"),
            ("ttforeign", "movie", "Foreign Movie", "Foreign Movie", 0, 2009, None, 110, "Drama"),
            ("ttnogenre", "movie", "No Genre", "No Genre", 0, 2010, None, 110, None),
            ("ttwatchlow", "movie", "Watch Low", "Watch Low", 0, 2011, None, 110, "Drama"),
        ]
        ratings = [
            ("ttkeepmovie", 8.0, 2000),
            ("ttlowvote", 8.0, 999),
            ("ttadult", 8.0, 5000),
            ("ttepisode", 8.0, 5000),
            ("ttseries", 8.0, 2500),
            ("ttmini", 8.0, 2500),
            ("tttvmovie", 8.0, 2500),
            ("ttshort", 8.0, 2500),
            ("ttforeign", 8.0, 5000),
            ("ttnogenre", 8.0, 5000),
            ("ttwatchlow", 8.0, 50),
        ]
        conn.executemany(
            """
            INSERT INTO titles (
                title_id, type, primary_title, original_title, is_adult,
                premiered, ended, runtime_minutes, genres
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            titles,
        )
        conn.executemany(
            "INSERT INTO ratings (title_id, rating, votes) VALUES (?, ?, ?)",
            ratings,
        )
        if with_akas:
            conn.executemany(
                """
                INSERT INTO akas (
                    title_id, title, region, language, types, attributes,
                    is_original_title
                )
                VALUES (?, ?, ?, ?, NULL, NULL, 0)
                """,
                [
                    ("ttkeepmovie", "Keep Movie", "US", "en"),
                    ("ttlowvote", "Low Vote", "US", "en"),
                    ("ttadult", "Adult Movie", "US", "en"),
                    ("ttepisode", "Episode", "US", "en"),
                    ("ttseries", "Keep Series", "US", "en"),
                    ("ttmini", "Keep Mini", "GB", "en"),
                    ("tttvmovie", "Keep TV Movie", "CA", None),
                    ("ttshort", "Short", "US", "en"),
                    ("ttforeign", "Foreign Movie", "IN", "hi"),
                    ("ttnogenre", "No Genre", "US", "en"),
                    ("ttwatchlow", "Watch Low", "US", "en"),
                ],
            )
        conn.execute(
            """
            INSERT INTO episodes (
                episode_title_id, show_title_id, season_number, episode_number
            )
            VALUES ('ttepisode', 'ttseries', 1, 1)
            """
        )
        conn.execute(
            """
            INSERT INTO external_ratings (
                title_id, metascore, rotten_tomatoes_score, omdb_imdb_rating,
                fetched_at
            )
            VALUES ('ttkeepmovie', 80, 90, 8.0, '2026-05-12T12:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO poster_cache (title_id, poster_url, tmdb_id, fetched_at)
            VALUES ('ttkeepmovie', 'https://image.tmdb.org/t/p/w342/keep.jpg', 123, '2026-05-12T12:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO users (id, username, password_hash, created_at)
            VALUES (1, 'admin', 'hash', '2026-05-12T12:00:00+00:00')
            """
        )
        conn.executemany(
            """
            INSERT INTO watchlist (user_id, title_id, status, notes, added_at)
            VALUES (1, ?, 'plan_to_watch', ?, '2026-05-12T12:00:00+00:00')
            """,
            [("ttkeepmovie", "keep"), ("ttwatchlow", "low but wanted")],
        )
        conn.commit()


def title_ids(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute("SELECT title_id FROM titles")}


def table_exists(path: Path, table: str) -> bool:
    with sqlite3.connect(path) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )


def test_slim_db_filters_low_votes_adult_and_disallowed_types(tmp_path: Path):
    source = tmp_path / "imdb.db"
    output = tmp_path / "imdb_slim.db"
    make_slim_source_db(source, with_akas=True)

    build_slim_database(source, output, hollywood_only=True, include_tv=True)

    ids = title_ids(output)
    assert {"ttkeepmovie", "ttseries", "ttmini", "tttvmovie", "ttnogenre"} <= ids
    assert "ttlowvote" not in ids
    assert "ttadult" not in ids
    assert "ttepisode" not in ids
    assert "ttshort" not in ids
    assert "ttforeign" not in ids


def test_slim_db_preserves_related_app_tables(tmp_path: Path):
    source = tmp_path / "imdb.db"
    output = tmp_path / "imdb_slim.db"
    make_slim_source_db(source, with_akas=True)

    build_slim_database(
        source,
        output,
        hollywood_only=True,
        include_tv=True,
        include_watchlist_always=True,
    )

    with sqlite3.connect(output) as conn:
        assert conn.execute("SELECT metascore FROM external_ratings").fetchall() == [(80,)]
        assert conn.execute("SELECT tmdb_id FROM poster_cache").fetchall() == [(123,)]
        assert conn.execute("SELECT username FROM users").fetchall() == [("admin",)]
        watch_titles = {
            row[0] for row in conn.execute("SELECT title_id FROM watchlist")
        }

    assert watch_titles == {"ttkeepmovie", "ttwatchlow"}
    assert "ttwatchlow" in title_ids(output)
    assert not table_exists(output, "episodes")
    assert not table_exists(output, "akas")


def test_slim_db_works_without_akas_using_approximate_filter(tmp_path: Path):
    source = tmp_path / "imdb.db"
    output = tmp_path / "imdb_slim.db"
    make_slim_source_db(source, with_akas=False)

    stats = build_slim_database(source, output, hollywood_only=True, include_tv=True)

    ids = title_ids(output)
    assert stats.approximate_language_filter is True
    assert "ttkeepmovie" in ids
    assert "ttforeign" in ids
    assert "ttnogenre" not in ids
    assert "ttepisode" not in ids

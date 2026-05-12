from __future__ import annotations

import re
import csv
import io
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from fetch_external_ratings import (
    build_fetch_plan,
    ensure_external_schema,
    parse_omdb_payload,
    parse_types,
)


@pytest.fixture()
def client(tmp_path: Path):
    yield from make_client(tmp_path, with_akas=True)


@pytest.fixture()
def client_without_akas(tmp_path: Path):
    yield from make_client(tmp_path, with_akas=False)


def make_client(tmp_path: Path, with_akas: bool):
    db_path = tmp_path / "imdb-test.db"
    build_test_db(db_path, with_akas=with_akas)

    app = create_app()
    app.config.update(
        DATABASE=db_path,
        SECRET_KEY="test-secret",
        TESTING=True,
    )
    with app.test_client() as test_client:
        yield test_client


def make_client_for_db(db_path: Path):
    app = create_app()
    app.config.update(
        DATABASE=db_path,
        SECRET_KEY="test-secret",
        TESTING=True,
    )
    return app.test_client()


def create_user(db_path: Path, username: str, password: str, user_id: int | None = None) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        if user_id is None:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, created_at)
                VALUES (?, ?, '2026-05-12T12:00:00+00:00')
                """,
                (username, generate_password_hash(password)),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (id, username, password_hash, created_at)
                VALUES (?, ?, ?, '2026-05-12T12:00:00+00:00')
                """,
                (user_id, username, generate_password_hash(password)),
            )
        conn.commit()


def login(client, username: str = "admin", password: str = "secret"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def build_test_db(path: Path, with_akas: bool) -> None:
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
            CREATE TABLE watchlist (
              title_id TEXT PRIMARY KEY,
              status TEXT NOT NULL DEFAULT 'plan_to_watch',
              notes TEXT NOT NULL DEFAULT '',
              added_at TEXT NOT NULL
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
            ("tt0000001", "movie", "Visible Movie", "Visible Movie", 0, 2001, None, 100, "Drama"),
            ("tt0000002", "movie", "Adult Movie", "Adult Movie", 1, 2002, None, 90, "Drama"),
            ("tt0000003", "tvSeries", "Visible Series", "Visible Series", 0, 2010, None, 45, "Drama"),
            ("tt0000004", "tvEpisode", "Episode Title", "Episode Title", 0, 2011, None, 45, "Drama"),
            ("tt0000005", "short", "Visible Short", "Visible Short", 0, 2012, None, 12, "Short"),
            ("tt0000006", "movie", "Hindi Movie", "Hindi Movie", 0, 2018, None, 120, "Drama"),
            ("tt0000007", "movie", "Hollywood Movie", "Hollywood Movie", 0, 2019, None, 110, "Drama"),
            ("tt0000008", "movie", "Sort Low", "Sort Low", 0, 2000, None, 100, "Drama"),
            ("tt0000009", "movie", "Sort High", "Sort High", 0, 2020, None, 100, "Drama"),
            ("tt0000010", "movie", "Sort Middle", "Sort Middle", 0, 2010, None, 100, "Drama"),
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
            [
                ("tt0000001", 7.0, 1000),
                ("tt0000002", 9.9, 5000),
                ("tt0000003", 8.0, 2000),
                ("tt0000004", 8.5, 1500),
                ("tt0000005", 6.0, 100),
                ("tt0000006", 7.2, 200),
                ("tt0000007", 7.4, 300),
                ("tt0000008", 3.0, 10),
                ("tt0000009", 9.0, 100),
                ("tt0000010", 6.0, 1000),
            ],
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
                    ("tt0000006", "Hindi Movie", "IN", "hi"),
                    ("tt0000007", "Hollywood Movie", "US", "en"),
                ],
            )
        conn.execute(
            """
            INSERT INTO episodes (
                episode_title_id, show_title_id, season_number, episode_number
            )
            VALUES ('tt0000004', 'tt0000003', 2, 5)
            """
        )
        conn.execute(
            """
            INSERT INTO watchlist (title_id, status, notes, added_at)
            VALUES ('tt0000001', 'plan_to_watch', '', '2026-05-10T12:00:00+00:00')
            """
        )
        for index in range(55):
            title_id = f"tt1{index:06d}"
            conn.execute(
                """
                INSERT INTO titles (
                    title_id, type, primary_title, original_title, is_adult,
                    premiered, ended, runtime_minutes, genres
                )
                VALUES (?, 'movie', ?, ?, 0, 2000, NULL, 100, 'Drama')
                """,
                (title_id, f"Paged Movie {index:02d}", f"Paged Movie {index:02d}"),
            )
            conn.execute(
                "INSERT INTO ratings (title_id, rating, votes) VALUES (?, 5.0, 10)",
                (title_id,),
            )
        conn.commit()


def add_quality_titles(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_external_schema(conn)
        conn.executemany(
            """
            INSERT INTO titles (
                title_id, type, primary_title, original_title, is_adult,
                premiered, ended, runtime_minutes, genres
            )
            VALUES (?, 'movie', ?, ?, 0, 2022, NULL, 100, 'Drama')
            """,
            [
                ("tt9000001", "Quality Full", "Quality Full"),
                ("tt9000002", "Quality Missing", "Quality Missing"),
                ("tt9000003", "Quality Low", "Quality Low"),
                ("tt9000004", "Quality Tiny Votes", "Quality Tiny Votes"),
                ("tt9000005", "Quality Missing Meta", "Quality Missing Meta"),
                ("tt9000006", "Quality Missing RT", "Quality Missing RT"),
                ("tt9000007", "Quality High Votes", "Quality High Votes"),
            ],
        )
        conn.executemany(
            "INSERT INTO ratings (title_id, rating, votes) VALUES (?, ?, ?)",
            [
                ("tt9000001", 8.0, 10000),
                ("tt9000002", 9.0, 10000),
                ("tt9000003", 5.0, 100),
                ("tt9000004", 9.8, 50),
                ("tt9000005", 8.0, 10000),
                ("tt9000006", 8.0, 10000),
                ("tt9000007", 9.0, 1000000),
            ],
        )
        conn.executemany(
            """
            INSERT INTO external_ratings (
                title_id, metascore, rotten_tomatoes_score,
                omdb_imdb_rating, fetched_at
            )
            VALUES (?, ?, ?, NULL, '2026-05-11T12:00:00+00:00')
            """,
            [
                ("tt9000001", 90, 90),
                ("tt9000003", 100, 100),
                ("tt9000005", None, 90),
                ("tt9000006", 90, None),
            ],
        )
        conn.commit()


def add_known_quality_examples(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_external_schema(conn)
        conn.executemany(
            """
            INSERT INTO titles (
                title_id, type, primary_title, original_title, is_adult,
                premiered, ended, runtime_minutes, genres
            )
            VALUES (?, 'movie', ?, ?, 0, 2022, NULL, 120, 'Drama')
            """,
            [
                ("tt9100001", "Known Shawshank", "Known Shawshank"),
                ("tt9100002", "Known Dark Knight", "Known Dark Knight"),
                ("tt9100003", "Known Audience Split", "Known Audience Split"),
                ("tt9100004", "Known Balanced Critics", "Known Balanced Critics"),
                ("tt9100005", "Known Low Vote 9.5", "Known Low Vote 9.5"),
                ("tt9100006", "Known Missing Meta", "Known Missing Meta"),
                ("tt9100007", "Known Full Meta", "Known Full Meta"),
            ],
        )
        conn.executemany(
            "INSERT INTO ratings (title_id, rating, votes) VALUES (?, ?, ?)",
            [
                ("tt9100001", 9.3, 3000000),
                ("tt9100002", 9.0, 2900000),
                ("tt9100003", 9.0, 500000),
                ("tt9100004", 8.4, 500000),
                ("tt9100005", 9.5, 45),
                ("tt9100006", 9.2, 200000),
                ("tt9100007", 9.2, 200000),
            ],
        )
        conn.executemany(
            """
            INSERT INTO external_ratings (
                title_id, metascore, rotten_tomatoes_score,
                omdb_imdb_rating, fetched_at
            )
            VALUES (?, ?, ?, NULL, '2026-05-11T12:00:00+00:00')
            """,
            [
                ("tt9100001", 82, 89),
                ("tt9100002", 84, 94),
                ("tt9100003", 55, 65),
                ("tt9100004", 90, 92),
                ("tt9100006", None, 96),
                ("tt9100007", 96, 96),
            ],
        )
        conn.commit()


def add_bulk_titles(db_path: Path, count: int) -> None:
    with sqlite3.connect(db_path) as conn:
        title_rows = []
        rating_rows = []
        for index in range(count):
            title_id = f"tt8{index:06d}"
            title = f"Bulk Movie {index:04d}"
            title_rows.append(
                (title_id, "movie", title, title, 0, 2000 + (index % 20), None, 100, "Drama")
            )
            rating_rows.append((title_id, 5.0 + (index % 40) / 10, 100 + index))
        conn.executemany(
            """
            INSERT INTO titles (
                title_id, type, primary_title, original_title, is_adult,
                premiered, ended, runtime_minutes, genres
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            title_rows,
        )
        conn.executemany(
            "INSERT INTO ratings (title_id, rating, votes) VALUES (?, ?, ?)",
            rating_rows,
        )
        conn.commit()


def quality_score_for(html: str, title: str) -> float:
    match = re.search(
        rf"<strong>{re.escape(title)}</strong>.*?"
        r'<span class="quality-value">([0-9]+\.[0-9])</span>',
        html,
        re.S,
    )
    assert match is not None, f"Quality score not found for {title}"
    return float(match.group(1))


def test_home_page_loads(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Browse IMDb" in response.data


def test_adult_titles_excluded_by_default(client):
    response = client.get("/?q=Adult&title_types=movie")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Adult Movie" not in html


def test_tv_episode_excluded_by_default(client):
    response = client.get("/?q=Episode")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Episode Title" not in html


def test_pagination_keeps_filters(client):
    response = client.get(
        "/?q=Paged&title_types=movie&genre=Drama&min_rating=1&min_votes=1&sort_by=title&sort_dir=asc&page_size=25&view=table"
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Next" in html

    href = extract_href(html, "Next")
    query = parse_qs(urlparse(href).query)

    assert query["q"] == ["Paged"]
    assert query["title_types"] == ["movie"]
    assert query["genre"] == ["Drama"]
    assert query["min_rating"] == ["1"]
    assert query["min_votes"] == ["1"]
    assert query["sort_by"] == ["title"]
    assert query["sort_dir"] == ["asc"]
    assert query["page_size"] == ["25"]
    assert query["view"] == ["table"]
    assert query["page"] == ["2"]


def test_table_view_loads(client):
    response = client.get("/?view=table")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="data-table"' in html
    assert "Title" in html


def test_sorting_by_imdb_rating_desc(client):
    response = client.get("/?q=Sort&title_types=movie&sort_by=rating&sort_dir=desc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.index("Sort High") < html.index("Sort Middle") < html.index("Sort Low")


def test_sorting_by_votes_asc(client):
    response = client.get("/?q=Sort&title_types=movie&sort_by=votes&sort_dir=asc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.index("Sort Low") < html.index("Sort High") < html.index("Sort Middle")


def test_page_size_works(client):
    response = client.get("/?q=Paged&title_types=movie&page_size=25")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.count('class="title-cell"') == 25


@pytest.mark.parametrize("page_size", [250, 500, 1000])
def test_large_page_sizes_work(tmp_path: Path, page_size: int):
    db_path = tmp_path / f"page-size-{page_size}.db"
    build_test_db(db_path, with_akas=True)
    add_bulk_titles(db_path, 1100)

    with make_client_for_db(db_path) as client:
        html = client.get(
            f"/?q=Bulk&title_types=movie&page_size={page_size}"
        ).get_data(as_text=True)

    assert html.count('class="title-cell"') == page_size


def test_show_all_allowed_below_limit(client):
    html = client.get("/?q=Paged&title_types=movie&page_size=all").get_data(
        as_text=True
    )

    assert "Too many results" not in html
    assert html.count('class="title-cell"') == 55
    assert "Showing 1-55 of 55 results" in html


def test_show_all_blocked_above_limit(tmp_path: Path):
    db_path = tmp_path / "show-all-blocked.db"
    build_test_db(db_path, with_akas=True)
    add_bulk_titles(db_path, 2100)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Bulk&title_types=movie&page_size=all").get_data(
            as_text=True
        )

    assert "Too many results to display at once. Narrow filters or export results." in html
    assert html.count('class="title-cell"') == 50
    assert "Showing 1-50 of 2,100 results" in html


def test_result_count_display(client):
    html = client.get("/?q=Paged&title_types=movie&page_size=25").get_data(
        as_text=True
    )

    assert "Showing 1-25 of 55 results" in html


def test_titles_csv_export_uses_filters_and_sort(client):
    response = client.get(
        "/titles.csv?q=Sort&title_types=movie&sort_by=rating&sort_dir=desc"
    )
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    assert response.status_code == 200
    assert [row["title"] for row in rows] == ["Sort High", "Sort Middle", "Sort Low"]
    assert rows[0]["quality_profile"] == "balanced"
    assert rows[0]["score_mode"] == "profile"


def test_invalid_sort_by_falls_back_safely(client):
    response = client.get("/?q=Sort&title_types=movie&sort_by=not_a_column&sort_dir=asc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.index("Sort Middle") < html.index("Sort High") < html.index("Sort Low")


def test_card_table_toggle_works(client):
    table = client.get("/?view=table").get_data(as_text=True)
    cards = client.get("/?view=cards").get_data(as_text=True)

    assert 'class="data-table"' in table
    assert 'class="result-card"' in cards


def test_quality_score_with_full_ratings(tmp_path: Path):
    db_path = tmp_path / "quality-full.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Quality+Full&title_types=movie").get_data(as_text=True)

    assert "Quality Full" in html
    assert "82.9" in html


def test_quality_score_with_missing_external_ratings(tmp_path: Path):
    db_path = tmp_path / "quality-missing.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Quality+Missing&title_types=movie").get_data(as_text=True)

    assert "Quality Missing" in html
    assert "71.6" in html


def test_quality_score_high_vote_title_gets_little_bayesian_penalty(tmp_path: Path):
    db_path = tmp_path / "quality-high-vote.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Quality+High+Votes&title_types=movie&show_score_breakdown=1"
        ).get_data(as_text=True)

    assert "Quality High Votes" in html
    assert "89.2" in html


def test_quality_score_low_vote_high_rated_title_gets_pulled_down(tmp_path: Path):
    db_path = tmp_path / "quality-low-vote.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Quality+Tiny+Votes&title_types=movie&show_score_breakdown=1"
        ).get_data(as_text=True)

    assert "Quality Tiny Votes" in html
    assert "64.4" in html


def test_quality_score_missing_metascore_does_not_become_zero(tmp_path: Path):
    db_path = tmp_path / "quality-missing-meta.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Quality+Missing+Meta&title_types=movie&show_score_breakdown=1"
        ).get_data(as_text=True)

    assert "Quality Missing Meta" in html
    assert "74.5" in html
    assert "Meta contribution" in html
    assert "N/A" in html


def test_quality_score_missing_rt_does_not_become_zero(tmp_path: Path):
    db_path = tmp_path / "quality-missing-rt.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Quality+Missing+RT&title_types=movie&show_score_breakdown=1"
        ).get_data(as_text=True)

    assert "Quality Missing RT" in html
    assert "77.3" in html
    assert "RT contribution" in html
    assert "N/A" in html


def test_quality_score_profiles_produce_different_scores(tmp_path: Path):
    db_path = tmp_path / "quality-profiles.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        balanced = client.get(
            "/?q=Quality+Full&title_types=movie&quality_profile=balanced"
        ).get_data(as_text=True)
        audience = client.get(
            "/?q=Quality+Full&title_types=movie&quality_profile=audience"
        ).get_data(as_text=True)
        critic = client.get(
            "/?q=Quality+Full&title_types=movie&quality_profile=critic"
        ).get_data(as_text=True)

    assert "82.9" in balanced
    assert "76.5" in audience
    assert "88.0" in critic


def test_quality_score_keeps_one_decimal_place(tmp_path: Path):
    db_path = tmp_path / "quality-decimal.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Quality+Full&title_types=movie").get_data(as_text=True)

    assert "82.9" in html
    assert "Quality 82.9" in html


def test_quality_score_sort(tmp_path: Path):
    db_path = tmp_path / "quality-sort.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Quality&title_types=movie&sort_by=quality_score&sort_dir=desc"
        ).get_data(as_text=True)

    assert (
        html.index("<strong>Quality High Votes</strong>")
        < html.index("<strong>Quality Low</strong>")
        < html.index("<strong>Quality Full</strong>")
        < html.index("<strong>Quality Missing</strong>")
        < html.index("<strong>Quality Tiny Votes</strong>")
    )


def test_quality_score_min_max_filters(tmp_path: Path):
    db_path = tmp_path / "quality-filter.db"
    build_test_db(db_path, with_akas=True)
    add_quality_titles(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Quality&title_types=movie&min_quality_score=82.8&max_quality_score=83"
        ).get_data(as_text=True)

    assert "Quality Full" in html
    assert "<strong>Quality Missing</strong>" not in html
    assert "<strong>Quality Low</strong>" not in html
    assert "<strong>Quality High Votes</strong>" not in html


def test_quality_known_titles_do_not_collapse_to_identical_scores(tmp_path: Path):
    db_path = tmp_path / "quality-known.db"
    build_test_db(db_path, with_akas=True)
    add_known_quality_examples(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Known&title_types=movie&sort_by=title&sort_dir=asc"
        ).get_data(as_text=True)

    shawshank = quality_score_for(html, "Known Shawshank")
    dark_knight = quality_score_for(html, "Known Dark Knight")
    assert abs(shawshank - dark_knight) >= 0.1


def test_quality_high_imdb_low_metascore_differs_from_balanced_critic_title(
    tmp_path: Path,
):
    db_path = tmp_path / "quality-known-balance.db"
    build_test_db(db_path, with_akas=True)
    add_known_quality_examples(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Known&title_types=movie").get_data(as_text=True)

    audience_split = quality_score_for(html, "Known Audience Split")
    balanced_critics = quality_score_for(html, "Known Balanced Critics")
    assert balanced_critics - audience_split > 10


def test_quality_known_low_vote_high_rating_is_penalized(tmp_path: Path):
    db_path = tmp_path / "quality-known-low-vote.db"
    build_test_db(db_path, with_akas=True)
    add_known_quality_examples(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Known+Low+Vote&title_types=movie").get_data(as_text=True)

    assert quality_score_for(html, "Known Low Vote 9.5") < 70


def test_quality_known_missing_metascore_does_not_overinflate(tmp_path: Path):
    db_path = tmp_path / "quality-known-missing-meta.db"
    build_test_db(db_path, with_akas=True)
    add_known_quality_examples(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Known&title_types=movie").get_data(as_text=True)

    missing_meta = quality_score_for(html, "Known Missing Meta")
    full_meta = quality_score_for(html, "Known Full Meta")
    assert missing_meta < full_meta


def test_quality_score_modes_change_scores(tmp_path: Path):
    db_path = tmp_path / "quality-modes.db"
    build_test_db(db_path, with_akas=True)
    add_known_quality_examples(db_path)

    with make_client_for_db(db_path) as client:
        profile = client.get(
            "/?q=Known+Audience+Split&title_types=movie&score_mode=profile"
        ).get_data(as_text=True)
        polarizing = client.get(
            "/?q=Known+Audience+Split&title_types=movie&score_mode=polarizing"
        ).get_data(as_text=True)

    assert quality_score_for(polarizing, "Known Audience Split") > quality_score_for(
        profile, "Known Audience Split"
    )


def test_compare_selected_shows_side_by_side_diagnostics(tmp_path: Path):
    db_path = tmp_path / "quality-compare.db"
    build_test_db(db_path, with_akas=True)
    add_known_quality_examples(db_path)

    with make_client_for_db(db_path) as client:
        html = client.get(
            "/?q=Known&title_types=movie&compare_ids=tt9100001&compare_ids=tt9100002"
        ).get_data(as_text=True)

    assert "Compare selected" in html
    assert "Known Shawshank" in html
    assert "Known Dark Knight" in html
    assert "Difference drivers" in html
    assert "Before rounding" in html


def test_sidebar_hidden_state_assets_do_not_break_layout(client):
    html = client.get("/").get_data(as_text=True)

    assert 'data-filters-toggle' in html
    assert 'data-filters-close' in html
    assert 'data-filters-backdrop' in html
    assert 'class="browser-shell view-table view-default"' in html
    assert 'class="filter-sidebar"' in html


def test_no_nested_vertical_scrollbar_layout_regressions(client):
    css = client.get("/static/styles.css").get_data(as_text=True)

    table_shell = re.search(r"\.table-shell \{(?P<body>.*?)\}", css, re.S)
    assert table_shell is not None
    assert "max-height" not in table_shell.group("body")
    assert "overflow: visible" in table_shell.group("body")


def test_table_does_not_create_internal_horizontal_scroll(client):
    css = client.get("/static/styles.css").get_data(as_text=True)

    table_shell = re.search(r"\.table-shell \{(?P<body>.*?)\}", css, re.S)
    data_table = re.search(r"\.data-table \{(?P<body>.*?)\}", css, re.S)
    assert table_shell is not None
    assert data_table is not None
    assert "overflow-x: auto" not in table_shell.group("body")
    assert "overflow-x: scroll" not in table_shell.group("body")
    assert "width: max-content" in table_shell.group("body")
    assert "width: max-content" in data_table.group("body")


def test_mobile_card_view_default_assets(client):
    html = client.get("/").get_data(as_text=True)
    css = client.get("/static/styles.css").get_data(as_text=True)

    assert 'class="browser-shell view-table view-default"' in html
    assert ".browser-shell.view-default .table-shell" in css
    assert ".browser-shell.view-default .mobile-card-fallback" in css


def test_sidebar_hidden_layout_uses_full_width(client):
    css = client.get("/static/styles.css").get_data(as_text=True)

    assert "body.filters-collapsed .browser-shell" in css
    assert "grid-template-columns: minmax(0, 1fr)" in css


def test_theme_toggle_assets_scripts_load_cleanly(client):
    html = client.get("/").get_data(as_text=True)
    script = client.get("/static/app.js")

    assert 'data-theme-select' in html
    assert "app.js" in html
    assert script.status_code == 200
    assert b"imdb-theme" in script.data
    assert b"localStorage" in script.data


def test_login_and_logout(tmp_path: Path):
    db_path = tmp_path / "auth.db"
    build_test_db(db_path, with_akas=True)
    create_user(db_path, "admin", "secret", user_id=1)

    with make_client_for_db(db_path) as client:
        logged_in = login(client)
        assert b"Logged in." in logged_in.data
        assert b"admin" in logged_in.data

        logged_out = client.post("/logout", follow_redirects=True)
        assert b"Logged out." in logged_out.data
        assert b"Login" in logged_out.data


def test_watchlist_page_requires_login(client):
    response = client.get("/watchlist")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_protected_watchlist_actions_require_login(client):
    response = client.post("/watchlist/add/tt0000001")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_watchlist_page_loads_when_logged_in(tmp_path: Path):
    db_path = tmp_path / "watchlist-login.db"
    build_test_db(db_path, with_akas=True)
    create_user(db_path, "admin", "secret", user_id=1)

    with make_client_for_db(db_path) as client:
        login(client)
        response = client.get("/watchlist")

    assert response.status_code == 200
    assert b"Watchlist" in response.data
    assert b"Visible Movie" in response.data


def test_watchlists_are_per_user(tmp_path: Path):
    db_path = tmp_path / "watchlist-users.db"
    build_test_db(db_path, with_akas=True)
    create_user(db_path, "admin", "secret", user_id=1)
    create_user(db_path, "other", "secret", user_id=2)

    with make_client_for_db(db_path) as client:
        login(client, "other", "secret")
        client.post("/watchlist/add/tt0000007", data={"status": "watched"})
        other_html = client.get("/watchlist").get_data(as_text=True)
        client.post("/logout")

        login(client, "admin", "secret")
        admin_html = client.get("/watchlist").get_data(as_text=True)

    assert "Hollywood Movie" in other_html
    assert "Visible Movie" not in other_html
    assert "Visible Movie" in admin_html
    assert "Hollywood Movie" not in admin_html


def test_poster_display_with_cached_data(tmp_path: Path):
    db_path = tmp_path / "poster.db"
    build_test_db(db_path, with_akas=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE poster_cache (
                title_id TEXT PRIMARY KEY,
                poster_url TEXT,
                tmdb_id INTEGER,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO poster_cache (title_id, poster_url, tmdb_id, fetched_at)
            VALUES ('tt0000001', 'https://image.tmdb.org/t/p/w342/example.jpg', 123, '2026-05-12T12:00:00+00:00')
            """
        )
        conn.commit()

    with make_client_for_db(db_path) as client:
        html = client.get("/?q=Visible+Movie&title_types=movie&view=cards").get_data(as_text=True)

    assert "https://image.tmdb.org/t/p/w342/example.jpg" in html
    assert "Poster for Visible Movie" in html


def test_poster_placeholder_without_cached_data(client):
    html = client.get("/?q=Visible+Movie&title_types=movie&view=cards").get_data(as_text=True)

    assert "No poster" in html


def test_invalid_filter_params_fall_back_safely(client):
    response = client.get(
        "/?title_types=bad&type=oops&quality_profile=nope&score_mode=nope&sort_by=drop_table&sort_dir=sideways&page_size=all"
    )
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Browse IMDb" in html
    assert 'name="sort_by" value="votes"' in html
    assert '<option value="profile" selected>Profile Score</option>' in html


def test_language_filter_disabled_without_akas(client_without_akas):
    response = client_without_akas.get("/?language_category=bollywood")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Language/region filtering requires rebuilding with --with-akas." in html
    assert 'name="language_category" disabled' in html


def test_language_filter_uses_akas_when_present(client):
    response = client.get("/?language_category=bollywood&title_types=movie")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Hindi Movie" in html
    assert "Hollywood Movie" not in html


def test_sorting_works(client):
    response = client.get("/?q=Sort&title_types=movie&sort_by=rating&sort_dir=desc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert html.index("Sort High") < html.index("Sort Middle") < html.index("Sort Low")


def test_metascore_empty_table_does_not_break(client):
    response = client.get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Metascore" in html


def test_omdb_parser_handles_na_values():
    parsed = parse_omdb_payload(
        {
            "Metascore": "N/A",
            "imdbRating": "N/A",
            "Ratings": [
                {"Source": "Internet Movie Database", "Value": "N/A"},
                {"Source": "Rotten Tomatoes", "Value": "N/A"},
            ],
        }
    )

    assert parsed == {
        "metascore": None,
        "rotten_tomatoes_score": None,
        "omdb_imdb_rating": None,
    }


def test_external_fetch_plan_filters_by_votes_type_and_adult(tmp_path: Path):
    db_path = tmp_path / "fetch-plan.db"
    build_test_db(db_path, with_akas=True)

    with sqlite3.connect(db_path) as conn:
        ensure_external_schema(conn)
        plan = build_fetch_plan(
            conn,
            limit=10,
            force=False,
            min_votes=500,
            title_types=parse_types("movie,tvSeries,tvMiniSeries,tvMovie"),
            watchlist_priority=False,
        )

    assert plan.title_ids == ["tt0000003", "tt0000010", "tt0000001"]
    assert "tt0000002" not in plan.title_ids
    assert "tt0000004" not in plan.title_ids
    assert "tt0000005" not in plan.title_ids


def test_external_fetch_plan_watchlist_priority_bypasses_votes(tmp_path: Path):
    db_path = tmp_path / "fetch-watchlist.db"
    build_test_db(db_path, with_akas=True)

    with sqlite3.connect(db_path) as conn:
        ensure_external_schema(conn)
        plan = build_fetch_plan(
            conn,
            limit=10,
            force=False,
            min_votes=5000,
            title_types=parse_types("movie,tvSeries,tvMiniSeries,tvMovie"),
            watchlist_priority=True,
        )

    assert plan.title_ids == ["tt0000001"]


def test_external_fetch_plan_skips_existing_rows(tmp_path: Path):
    db_path = tmp_path / "fetch-skip.db"
    build_test_db(db_path, with_akas=True)

    with sqlite3.connect(db_path) as conn:
        ensure_external_schema(conn)
        conn.execute(
            """
            INSERT INTO external_ratings (
                title_id, metascore, rotten_tomatoes_score,
                omdb_imdb_rating, fetched_at
            )
            VALUES ('tt0000003', 80, 90, 8.0, '2026-05-11T12:00:00+00:00')
            """
        )
        plan = build_fetch_plan(
            conn,
            limit=10,
            force=False,
            min_votes=500,
            title_types=parse_types("movie,tvSeries,tvMiniSeries,tvMovie"),
            watchlist_priority=False,
        )

    assert plan.skipped == 1
    assert plan.title_ids == ["tt0000010", "tt0000001"]


def extract_href(html: str, link_text: str) -> str:
    marker = f">{link_text}</a>"
    end = html.index(marker)
    start = html.rfind('href="', 0, end) + len('href="')
    href_end = html.index('"', start)
    return html[start:href_end].replace("&amp;", "&")

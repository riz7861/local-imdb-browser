from __future__ import annotations

import csv
import functools
import io
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from db_paths import (
    BASE_DIR,
    DEFAULT_DB_PATH,
    DatabaseBootstrapError,
    bootstrap_database_from_download_url,
    configured_database_path,
)

PER_PAGE = 50
PAGE_SIZE_OPTIONS = [25, 50, 100, 250, 500, 1000, "all"]
DEFAULT_PAGE_SIZE = 50
SHOW_ALL_SAFE_LIMIT = 2000
SLOW_QUERY_SECONDS = 0.5
LOGGER = logging.getLogger(__name__)
DEV_SECRET_KEY = secrets.token_urlsafe(32)

DEFAULT_TITLE_TYPE_FILTERS = ["movie", "tvSeries", "tvMiniSeries", "tvMovie"]

TITLE_TYPE_OPTIONS = [
    {"value": "movie", "label": "Movie", "types": ["movie"], "genre": ""},
    {"value": "tvSeries", "label": "TV Series", "types": ["tvSeries"], "genre": ""},
    {
        "value": "tvMiniSeries",
        "label": "Mini-series",
        "types": ["tvMiniSeries"],
        "genre": "",
    },
    {"value": "tvMovie", "label": "TV Movie", "types": ["tvMovie"], "genre": ""},
    {"value": "tvEpisode", "label": "Episode", "types": ["tvEpisode"], "genre": ""},
    {"value": "short", "label": "Short", "types": ["short"], "genre": ""},
    {"value": "documentary", "label": "Documentary", "types": [], "genre": "Documentary"},
]
TITLE_TYPE_VALUES = {option["value"] for option in TITLE_TYPE_OPTIONS}

GENRES = [
    "Action",
    "Adult",
    "Adventure",
    "Animation",
    "Biography",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "Film-Noir",
    "Game-Show",
    "History",
    "Horror",
    "Music",
    "Musical",
    "Mystery",
    "News",
    "Reality-TV",
    "Romance",
    "Sci-Fi",
    "Short",
    "Sport",
    "Talk-Show",
    "Thriller",
    "War",
    "Western",
]

WATCH_STATUSES = [
    ("plan_to_watch", "Plan to watch"),
    ("watching", "Watching"),
    ("watched", "Watched"),
    ("paused", "Paused"),
    ("dropped", "Dropped"),
]
WATCH_STATUS_VALUES = {value for value, _label in WATCH_STATUSES}

LANGUAGE_GROUPS = {
    "hollywood": {
        "label": "Hollywood / English",
        "regions": ["US", "GB", "CA", "AU"],
        "languages": ["en"],
    },
    "bollywood": {
        "label": "Bollywood / Hindi",
        "regions": ["IN"],
        "languages": ["hi"],
    },
    "turkish": {
        "label": "Turkish",
        "regions": ["TR"],
        "languages": ["tr"],
    },
    "arabic": {
        "label": "Arabic",
        "regions": ["EG", "SA", "AE", "LB", "MA", "DZ"],
        "languages": ["ar"],
    },
}
LANGUAGE_GROUP_VALUES = set(LANGUAGE_GROUPS)

SORT_OPTIONS = [
    ("quality_score_desc", "Quality Score high to low"),
    ("quality_score_asc", "Quality Score low to high"),
    ("votes_desc", "Vote count high to low"),
    ("votes_asc", "Vote count low to high"),
    ("rating_desc", "IMDb rating high to low"),
    ("rating_asc", "IMDb rating low to high"),
    ("year_desc", "Year newest first"),
    ("year_asc", "Year oldest first"),
    ("title_asc", "Title A-Z"),
]
SORT_VALUES = {value for value, _label in SORT_OPTIONS}
DEFAULT_SORT = "votes_desc"
SORT_COLUMN_OPTIONS = [
    ("title", "Title"),
    ("type", "Type"),
    ("year", "Year"),
    ("rating", "IMDb rating"),
    ("votes", "IMDb votes"),
    ("metascore", "Metascore"),
    ("rotten_tomatoes", "Rotten Tomatoes"),
    ("quality_score", "Quality Score"),
]
QUALITY_CONFIDENCE_THRESHOLD = 25000.0
QUALITY_GLOBAL_MEAN_SCORE = 65.0
QUALITY_PROFILES = {
    "balanced": {
        "label": "Balanced",
        "weights": {
            "imdb": 0.45,
            "metascore": 0.30,
            "rotten_tomatoes": 0.20,
            "vote_confidence": 0.05,
        },
    },
    "audience": {
        "label": "Audience-heavy",
        "weights": {
            "imdb": 0.70,
            "metascore": 0.15,
            "rotten_tomatoes": 0.10,
            "vote_confidence": 0.05,
        },
    },
    "critic": {
        "label": "Critic-heavy",
        "weights": {
            "imdb": 0.25,
            "metascore": 0.45,
            "rotten_tomatoes": 0.25,
            "vote_confidence": 0.05,
        },
    },
}
QUALITY_PROFILE_OPTIONS = [
    (value, profile["label"]) for value, profile in QUALITY_PROFILES.items()
]
QUALITY_PROFILE_VALUES = set(QUALITY_PROFILES)
DEFAULT_QUALITY_PROFILE = "balanced"
QUALITY_SCORE_MODES = {
    "profile": "Profile Score",
    "consensus": "Consensus Score",
    "polarizing": "Polarizing Score",
    "hidden_gem": "Hidden Gem Score",
}
QUALITY_SCORE_MODE_OPTIONS = list(QUALITY_SCORE_MODES.items())
DEFAULT_QUALITY_SCORE_MODE = "profile"
QUALITY_SELECT_FIELDS = [
    "quality_score",
    "quality_score_unrounded",
    "quality_base_score",
    "quality_mode_adjustment",
    "raw_imdb_score",
    "adjusted_imdb_score",
    "imdb_score_contribution",
    "metascore_used",
    "metascore_score_contribution",
    "rotten_tomatoes_score_used",
    "rotten_tomatoes_score_contribution",
    "vote_confidence_score",
    "vote_confidence_score_contribution",
    "audience_critic_gap",
    "source_spread",
    "quality_missing_summary",
]
SORT_COLUMN_SQL = {
    "title": "t.primary_title COLLATE NOCASE",
    "type": "t.type COLLATE NOCASE",
    "year": "t.premiered",
    "rating": "r.rating",
    "votes": "r.votes",
    "metascore": "er.metascore",
    "rotten_tomatoes": "er.rotten_tomatoes_score",
}
DEFAULT_SORT_BY = "votes"
DEFAULT_SORT_DIR = "desc"
VIEW_OPTIONS = {"table", "cards"}
DEFAULT_VIEW = "table"


def quality_score_sql_parts(
    profile: str | None = None, mode: str | None = None
) -> dict[str, str]:
    weights = QUALITY_PROFILES.get(
        profile or DEFAULT_QUALITY_PROFILE,
        QUALITY_PROFILES[DEFAULT_QUALITY_PROFILE],
    )["weights"]
    mode = mode if mode in QUALITY_SCORE_MODES else DEFAULT_QUALITY_SCORE_MODE
    confidence_threshold = f"{QUALITY_CONFIDENCE_THRESHOLD:.1f}"
    global_mean = f"{QUALITY_GLOBAL_MEAN_SCORE:.1f}"
    vote_ratio = (
        f"(COALESCE(r.votes, 0) * 1.0 / "
        f"(COALESCE(r.votes, 0) + {confidence_threshold}))"
    )
    raw_imdb = "(CASE WHEN r.rating IS NULL THEN NULL ELSE (r.rating * 10.0) END)"
    adjusted_imdb = f"""
        (
            CASE
                WHEN r.rating IS NULL THEN NULL
                ELSE (({vote_ratio} * {raw_imdb})
                    + ((1.0 - {vote_ratio}) * {global_mean}))
            END
        )
    """
    external_average = """
        (
            CASE
                WHEN er.metascore IS NOT NULL
                     AND er.rotten_tomatoes_score IS NOT NULL
                    THEN (er.metascore + er.rotten_tomatoes_score) / 2.0
                ELSE COALESCE(er.metascore * 1.0, er.rotten_tomatoes_score * 1.0)
            END
        )
    """
    fallback_score = f"""
        (
            CASE
                WHEN {adjusted_imdb} IS NOT NULL
                    THEN ({adjusted_imdb} * 0.60) + ({global_mean} * 0.40)
                ELSE COALESCE({external_average}, {global_mean})
            END
        )
    """
    imdb_used = f"COALESCE({adjusted_imdb}, {external_average}, {global_mean})"
    metascore = "(er.metascore * 1.0)"
    rotten_tomatoes = "(er.rotten_tomatoes_score * 1.0)"
    metascore_used = f"COALESCE({metascore}, {fallback_score})"
    rotten_tomatoes_used = f"COALESCE({rotten_tomatoes}, {fallback_score})"
    vote_confidence = f"""
        (
            CASE
                WHEN r.rating IS NULL THEN NULL
                ELSE ({vote_ratio} * 100.0)
            END
        )
    """
    vote_component = f"""
        (
            CASE
                WHEN r.rating IS NULL THEN 55.0
                ELSE 55.0 + ({vote_ratio} * 35.0)
            END
        )
    """

    def weighted_component(expr: str, weight: float) -> str:
        return f"ROUND(({expr}) * {weight:.4f}, 1)"

    weighted_sum = f"""
        (
            ({imdb_used} * {weights["imdb"]:.4f})
            + ({metascore_used} * {weights["metascore"]:.4f})
            + ({rotten_tomatoes_used} * {weights["rotten_tomatoes"]:.4f})
            + ({vote_component} * {weights["vote_confidence"]:.4f})
        )
    """
    spread_score = f"(65.0 + (({weighted_sum} - 65.0) * 1.24))"
    source_max = f"MAX({imdb_used}, {metascore_used}, {rotten_tomatoes_used})"
    source_min = f"MIN({imdb_used}, {metascore_used}, {rotten_tomatoes_used})"
    source_spread = f"(({source_max}) - ({source_min}))"
    critic_average = f"(({metascore_used} + {rotten_tomatoes_used}) / 2.0)"
    audience_critic_gap = f"ABS({imdb_used} - {critic_average})"

    def positive_delta(expr: str) -> str:
        return f"(CASE WHEN {expr} > 0 THEN {expr} ELSE 0.0 END)"

    def lower_of(expr: str, limit: float) -> str:
        return f"(CASE WHEN {expr} < {limit:.1f} THEN {expr} ELSE {limit:.1f} END)"

    def clamp_score(expr: str) -> str:
        return f"""
            (
                CASE
                    WHEN {expr} < 0.0 THEN 0.0
                    WHEN {expr} > 100.0 THEN 100.0
                    ELSE {expr}
                END
            )
        """

    profile_score = clamp_score(spread_score)
    consensus_adjustment = f"""
        (
            ((18.0 - {lower_of(source_spread, 18.0)}) * 0.35)
            - ({positive_delta(f"{source_spread} - 25.0")} * 0.25)
        )
    """
    consensus_score = clamp_score(f"({profile_score} + {consensus_adjustment})")
    polarizing_adjustment = f"""
        (
            ({lower_of(source_spread, 35.0)} * 0.45)
            + ({lower_of(audience_critic_gap, 35.0)} * 0.25)
        )
    """
    polarizing_score = clamp_score(f"({profile_score} + {polarizing_adjustment})")
    credible_vote_ratio = (
        "(COALESCE(r.votes, 0) * 1.0 / (COALESCE(r.votes, 0) + 2000.0))"
    )
    scarcity_ratio = "(1.0 - (COALESCE(r.votes, 0) * 1.0 / (COALESCE(r.votes, 0) + 50000.0)))"
    hidden_gem_adjustment = f"""
        (
            CASE
                WHEN COALESCE(r.votes, 0) < 500 THEN -12.0
                ELSE (
                    {positive_delta(f"{weighted_sum} - 70.0")}
                    * 0.90
                    * {credible_vote_ratio}
                    * {scarcity_ratio}
                ) - ((COALESCE(r.votes, 0) * 1.0 / (COALESCE(r.votes, 0) + 250000.0)) * 8.0)
            END
        )
    """
    hidden_gem_score = clamp_score(f"({profile_score} + {hidden_gem_adjustment})")
    mode_score = {
        "profile": profile_score,
        "consensus": consensus_score,
        "polarizing": polarizing_score,
        "hidden_gem": hidden_gem_score,
    }[mode]
    mode_adjustment = f"(({mode_score}) - ({profile_score}))"
    missing_summary = f"""
        (
            CASE
                WHEN r.rating IS NULL
                     AND er.metascore IS NULL
                     AND er.rotten_tomatoes_score IS NULL
                    THEN 'All ratings missing; global fallback used'
                WHEN er.metascore IS NULL
                     AND er.rotten_tomatoes_score IS NULL
                    THEN 'External ratings missing; conservative IMDb/global fallback used'
                WHEN er.metascore IS NULL
                    THEN 'Metascore missing; conservative fallback used'
                WHEN er.rotten_tomatoes_score IS NULL
                    THEN 'Rotten Tomatoes missing; conservative fallback used'
                WHEN r.rating IS NULL
                    THEN 'IMDb rating missing; external ratings used'
                ELSE 'All rating sources available'
            END
        )
    """

    return {
        "quality_score": f"ROUND({mode_score}, 1)",
        "quality_score_unrounded": mode_score,
        "quality_base_score": f"ROUND({weighted_sum}, 3)",
        "quality_mode_adjustment": f"ROUND({mode_adjustment}, 3)",
        "raw_imdb_score": f"ROUND({raw_imdb}, 1)",
        "adjusted_imdb_score": f"ROUND({adjusted_imdb}, 1)",
        "imdb_score_contribution": weighted_component(
            imdb_used, weights["imdb"]
        ),
        "metascore_used": f"ROUND({metascore_used}, 1)",
        "metascore_score_contribution": weighted_component(
            metascore_used, weights["metascore"]
        ),
        "rotten_tomatoes_score_used": f"ROUND({rotten_tomatoes_used}, 1)",
        "rotten_tomatoes_score_contribution": weighted_component(
            rotten_tomatoes_used, weights["rotten_tomatoes"]
        ),
        "vote_confidence_score": f"ROUND({vote_confidence}, 1)",
        "vote_confidence_score_contribution": weighted_component(
            vote_component, weights["vote_confidence"]
        ),
        "audience_critic_gap": f"ROUND({audience_critic_gap}, 1)",
        "source_spread": f"ROUND({source_spread}, 1)",
        "quality_missing_summary": missing_summary,
    }


def quality_select_sql(
    parts: dict[str, str], include_diagnostics: bool = False
) -> str:
    fields: list[str] = []
    for field in QUALITY_SELECT_FIELDS:
        if field == "quality_score" or include_diagnostics:
            fields.append(f"{parts[field]} AS {field}")
        else:
            fields.append(f"NULL AS {field}")
    return ",\n            ".join(fields)


def create_app() -> Flask:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
    app.config["DATABASE"] = configured_database_path()
    app.config["DATABASE_BOOTSTRAP_ERROR"] = ""

    try:
        bootstrap_database_from_download_url(app.config["DATABASE"], logger=app.logger)
    except DatabaseBootstrapError as exc:
        app.config["DATABASE_BOOTSTRAP_ERROR"] = str(exc)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "db_path": app.config["DATABASE"],
            "genres": GENRES,
            "title_type_options": TITLE_TYPE_OPTIONS,
            "watch_statuses": WATCH_STATUSES,
            "language_groups": LANGUAGE_GROUPS,
            "sort_options": SORT_OPTIONS,
            "page_size_options": PAGE_SIZE_OPTIONS,
            "quality_profile_options": QUALITY_PROFILE_OPTIONS,
            "quality_score_mode_options": QUALITY_SCORE_MODE_OPTIONS,
            "current_user": current_user(),
        }

    @app.teardown_appcontext
    def close_db(_error: BaseException | None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.route("/")
    def index() -> str:
        ready, reason = database_ready()
        if not ready:
            return render_template("setup_needed.html", reason=reason)

        ensure_watchlist_schema()
        adult_unlocked = is_adult_unlocked()
        akas_available = table_exists("akas")
        episodes_available = table_exists("episodes")
        filters = read_title_filters(request.args, adult_unlocked=adult_unlocked)
        disable_episode_filters(filters, episodes_available)
        view_explicit = bool(request.args.get("view"))
        page = positive_int(request.args.get("page"), default=1)
        page_data = find_titles(
            filters,
            page,
            akas_available=akas_available,
            episodes_available=episodes_available,
        )
        rows = page_data["rows"]
        has_next = page_data["has_next"]
        page = page_data["page"]
        compare_rows = (
            find_compare_titles(filters, episodes_available=episodes_available)
            if compare_ready(filters)
            else []
        )
        prev_args = page_query_args(filters, page - 1) if page > 1 else {}
        next_args = page_query_args(filters, page + 1) if has_next else {}
        sort_controls = build_sort_controls(filters)

        return render_template(
            "index.html",
            filters=filters,
            rows=rows,
            result_start=page_data["result_start"],
            result_end=page_data["result_end"],
            total_count=page_data["total_count"],
            all_blocked=page_data["all_blocked"],
            query_ms=page_data["query_ms"],
            show_all_limit=SHOW_ALL_SAFE_LIMIT,
            compare_rows=compare_rows,
            compare_ready=compare_ready(filters),
            compare_invalid=bool(filters.get("compare_ids")) and not compare_ready(filters),
            clear_compare_url=url_for("index", **clear_compare_args(filters)),
            page=page,
            has_next=has_next,
            view=filters["view"],
            view_explicit=view_explicit,
            adult_unlocked=adult_unlocked,
            akas_available=akas_available,
            episodes_available=episodes_available,
            language_filter_unavailable=language_filter_unavailable(
                filters, akas_available
            ),
            title_type_options=title_type_options_for(episodes_available),
            sort_controls=sort_controls,
            sort_control_map={item["value"]: item for item in sort_controls},
            table_url=url_for("index", **view_query_args(filters, "table")),
            cards_url=url_for("index", **view_query_args(filters, "cards")),
            export_url=url_for("titles_csv", **active_filter_args(filters)),
            prev_args=prev_args,
            next_args=next_args,
            prev_url=url_for("index", **prev_args) if prev_args else "",
            next_url=url_for("index", **next_args) if next_args else "",
            first_url=url_for("index", **page_query_args(filters, 1)),
        )

    @app.route("/adult-content")
    def adult_content_lock() -> str:
        return render_template("adult_lock.html", adult_unlocked=is_adult_unlocked())

    @app.post("/adult-content/unlock")
    def unlock_adult_content() -> Response:
        session["adult_unlocked"] = True
        flash("Adult title filter unlocked for this browser session.", "success")
        return redirect(safe_next_url())

    @app.post("/adult-content/lock")
    def lock_adult_content() -> Response:
        session.pop("adult_unlocked", None)
        flash("Adult titles are hidden again.", "success")
        return redirect(safe_next_url())

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str | Response:
        ready, reason = database_ready()
        if not ready:
            return render_template("setup_needed.html", reason=reason)
        ensure_watchlist_schema()
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = get_db().execute(
                "SELECT id, username, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                flash("Logged in.", "success")
                return redirect(safe_next_url())
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout() -> Response:
        session.pop("user_id", None)
        flash("Logged out.", "success")
        return redirect(url_for("index"))

    @app.post("/watchlist/add/<title_id>")
    @login_required
    def add_to_watchlist(title_id: str) -> Response:
        if not database_ready()[0]:
            flash("Build imdb.db before using the watchlist.", "error")
            return redirect(url_for("index"))

        ensure_watchlist_schema()
        status = normalize_status(request.form.get("status"))
        notes = (request.form.get("notes") or "").strip()
        added_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        db = get_db()
        exists = db.execute(
            "SELECT 1 FROM titles WHERE title_id = ? LIMIT 1", (title_id,)
        ).fetchone()
        if not exists:
            flash("That IMDb title was not found in the local database.", "error")
            return redirect(safe_next_url())

        db.execute(
            """
            INSERT INTO watchlist (user_id, title_id, status, notes, added_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, title_id) DO UPDATE SET
                status = excluded.status,
                notes = excluded.notes
            """,
            (current_user_id(), title_id, status, notes, added_at),
        )
        db.commit()
        flash("Added to watchlist.", "success")
        return redirect(safe_next_url())

    @app.post("/watchlist/update/<title_id>")
    @login_required
    def update_watchlist(title_id: str) -> Response:
        if not database_ready()[0]:
            flash("Build imdb.db before using the watchlist.", "error")
            return redirect(url_for("index"))

        ensure_watchlist_schema()
        status = normalize_status(request.form.get("status"))
        notes = (request.form.get("notes") or "").strip()
        get_db().execute(
            """
            UPDATE watchlist
            SET status = ?, notes = ?
            WHERE user_id = ? AND title_id = ?
            """,
            (status, notes, current_user_id(), title_id),
        )
        get_db().commit()
        flash("Watchlist item updated.", "success")
        return redirect(safe_next_url(default_endpoint="watchlist"))

    @app.post("/watchlist/remove/<title_id>")
    @login_required
    def remove_from_watchlist(title_id: str) -> Response:
        if not database_ready()[0]:
            flash("Build imdb.db before using the watchlist.", "error")
            return redirect(url_for("index"))

        ensure_watchlist_schema()
        get_db().execute(
            "DELETE FROM watchlist WHERE user_id = ? AND title_id = ?",
            (current_user_id(), title_id),
        )
        get_db().commit()
        flash("Removed from watchlist.", "success")
        return redirect(safe_next_url(default_endpoint="watchlist"))

    @app.route("/watchlist")
    @login_required
    def watchlist() -> str:
        ready, reason = database_ready()
        if not ready:
            return render_template("setup_needed.html", reason=reason)

        ensure_watchlist_schema()
        adult_unlocked = is_adult_unlocked()
        akas_available = table_exists("akas")
        episodes_available = table_exists("episodes")
        filters = read_watchlist_filters(request.args, adult_unlocked=adult_unlocked)
        disable_episode_filters(filters, episodes_available)
        page = positive_int(request.args.get("page"), default=1)
        rows, has_next = find_watchlist(
            filters,
            page,
            akas_available=akas_available,
            episodes_available=episodes_available,
        )
        prev_args = page_query_args(filters, page - 1) if page > 1 else {}
        next_args = page_query_args(filters, page + 1) if has_next else {}

        return render_template(
            "watchlist.html",
            filters=filters,
            rows=rows,
            page=page,
            has_next=has_next,
            adult_unlocked=adult_unlocked,
            akas_available=akas_available,
            episodes_available=episodes_available,
            language_filter_unavailable=language_filter_unavailable(
                filters, akas_available
            ),
            title_type_options=title_type_options_for(episodes_available),
            prev_args=prev_args,
            next_args=next_args,
            prev_url=url_for("watchlist", **prev_args) if prev_args else "",
            next_url=url_for("watchlist", **next_args) if next_args else "",
            export_url=url_for("watchlist_csv", **active_filter_args(filters)),
        )

    @app.route("/watchlist.csv")
    @login_required
    def watchlist_csv() -> Response:
        ready, reason = database_ready()
        if not ready:
            return Response(reason, status=503, mimetype="text/plain")

        ensure_watchlist_schema()
        filters = read_watchlist_filters(
            request.args, adult_unlocked=is_adult_unlocked()
        )
        episodes_available = table_exists("episodes")
        disable_episode_filters(filters, episodes_available)
        rows = find_watchlist_for_export(
            filters,
            akas_available=table_exists("akas"),
            episodes_available=episodes_available,
        )
        csv_data = render_watchlist_csv(rows)
        filename = f"watchlist-{datetime.now().date().isoformat()}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.route("/titles.csv")
    def titles_csv() -> Response:
        ready, reason = database_ready()
        if not ready:
            return Response(reason, status=503, mimetype="text/plain")

        ensure_watchlist_schema()
        filters = read_title_filters(
            request.args, adult_unlocked=is_adult_unlocked()
        )
        episodes_available = table_exists("episodes")
        disable_episode_filters(filters, episodes_available)
        rows = find_titles_for_export(
            filters,
            akas_available=table_exists("akas"),
            episodes_available=episodes_available,
        )
        csv_data = render_titles_csv(rows, filters)
        filename = f"titles-{datetime.now().date().isoformat()}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    app.jinja_env.globals["imdb_url"] = imdb_url
    app.jinja_env.globals["episode_code"] = episode_code
    app.jinja_env.globals["title_type_label"] = title_type_label
    app.jinja_env.globals["quality_profile_label"] = quality_profile_label
    app.jinja_env.globals["quality_score_mode_label"] = quality_score_mode_label
    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = Path(current_app_config("DATABASE"))
        g.db = sqlite3.connect(path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def current_app_config(key: str) -> Any:
    from flask import current_app

    return current_app.config[key]


def database_ready() -> tuple[bool, str]:
    path = Path(current_app_config("DATABASE"))
    if not path.exists():
        bootstrap_error = current_app_config("DATABASE_BOOTSTRAP_ERROR")
        if bootstrap_error:
            return False, bootstrap_error
        return False, f"Database not found at {path}"

    try:
        db = get_db()
        found = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'titles'"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        return False, f"Could not read {path}: {exc}"

    if not found:
        return False, f"{path} exists, but it does not contain the IMDb titles table"
    return True, ""


def table_exists(name: str) -> bool:
    return (
        get_db()
        .execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        )
        .fetchone()
        is not None
    )


def login_required(view: Any) -> Any:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not current_user_id():
            flash("Log in to use your watchlist.", "error")
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def current_user_id() -> int | None:
    user_id = session.get("user_id")
    try:
        return int(user_id) if user_id else None
    except (TypeError, ValueError):
        return None


def current_user() -> sqlite3.Row | None:
    user_id = current_user_id()
    if not user_id:
        return None
    if "current_user" not in g:
        try:
            g.current_user = get_db().execute(
                "SELECT id, username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        except sqlite3.DatabaseError:
            g.current_user = None
    return g.current_user


def ensure_watchlist_schema() -> None:
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
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
    migrate_watchlist_schema(db)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_user_status ON watchlist(user_id, status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_user_added_at ON watchlist(user_id, added_at)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_title_id ON watchlist(title_id)")
    db.execute(
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
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_metascore ON external_ratings(metascore)"
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_external_rotten_tomatoes
        ON external_ratings(rotten_tomatoes_score)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS poster_cache (
            title_id TEXT PRIMARY KEY,
            poster_url TEXT,
            tmdb_id INTEGER,
            fetched_at TEXT NOT NULL
        )
        """
    )
    create_local_app_indexes(db)
    db.commit()


def migrate_watchlist_schema(db: sqlite3.Connection) -> None:
    columns = [row["name"] for row in db.execute("PRAGMA table_info(watchlist)")]
    if "user_id" in columns:
        return
    db.execute("ALTER TABLE watchlist RENAME TO watchlist_legacy")
    db.execute(
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
    db.execute(
        """
        INSERT OR IGNORE INTO watchlist (user_id, title_id, status, notes, added_at)
        SELECT 1, title_id, status, notes, added_at
        FROM watchlist_legacy
        """
    )
    db.execute("DROP TABLE watchlist_legacy")


def create_local_app_indexes(db: sqlite3.Connection) -> None:
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_external_metascore_title ON external_ratings(metascore, title_id)",
        "CREATE INDEX IF NOT EXISTS idx_external_rt_title ON external_ratings(rotten_tomatoes_score, title_id)",
        "CREATE INDEX IF NOT EXISTS idx_poster_cache_title ON poster_cache(title_id)",
    ]
    for statement in index_statements:
        db.execute(statement)


def read_title_filters(args: Any, adult_unlocked: bool = False) -> dict[str, Any]:
    sort_by, sort_dir = clean_sort(args)
    return {
        "title_types": clean_title_types(args),
        "start_year": clean_int_string(args.get("start_year")),
        "end_year": clean_int_string(args.get("end_year")),
        "genre": clean_choice(args.get("genre"), GENRES),
        "min_rating": clean_float_string(args.get("min_rating")),
        "max_rating": clean_float_string(args.get("max_rating")),
        "min_votes": clean_int_string(args.get("min_votes")),
        "max_votes": clean_int_string(args.get("max_votes")),
        "min_metascore": clean_int_string(args.get("min_metascore")),
        "max_metascore": clean_int_string(args.get("max_metascore")),
        "min_rotten_tomatoes": clean_int_string(args.get("min_rotten_tomatoes")),
        "max_rotten_tomatoes": clean_int_string(args.get("max_rotten_tomatoes")),
        "min_quality_score": clean_float_string(args.get("min_quality_score")),
        "max_quality_score": clean_float_string(args.get("max_quality_score")),
        "q": (args.get("q") or "").strip(),
        "language_category": clean_choice(
            args.get("language_category"), list(LANGUAGE_GROUP_VALUES)
        ),
        "quality_profile": clean_choice(
            args.get("quality_profile"), list(QUALITY_PROFILE_VALUES)
        )
        or DEFAULT_QUALITY_PROFILE,
        "score_mode": clean_choice(
            args.get("score_mode"), list(QUALITY_SCORE_MODES)
        )
        or DEFAULT_QUALITY_SCORE_MODE,
        "show_score_breakdown": "1" if args.get("show_score_breakdown") == "1" else "",
        "compare_ids": clean_title_ids(args.getlist("compare_ids"))
        if hasattr(args, "getlist")
        else [],
        "sort": clean_choice(args.get("sort"), list(SORT_VALUES)) or DEFAULT_SORT,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "page_size": clean_page_size(args.get("page_size")),
        "view": clean_choice(args.get("view"), list(VIEW_OPTIONS)) or DEFAULT_VIEW,
        "include_adult": "1" if adult_unlocked and args.get("include_adult") == "1" else "",
    }


def read_watchlist_filters(args: Any, adult_unlocked: bool = False) -> dict[str, Any]:
    filters = read_title_filters(args, adult_unlocked=adult_unlocked)
    filters["status"] = clean_choice(args.get("status"), list(WATCH_STATUS_VALUES))
    return filters


def disable_episode_filters(filters: dict[str, Any], episodes_available: bool) -> None:
    if episodes_available:
        return
    filters["title_types"] = [
        value for value in filters.get("title_types", []) if value != "tvEpisode"
    ]
    if not filters["title_types"]:
        filters["title_types"] = DEFAULT_TITLE_TYPE_FILTERS.copy()


def title_type_options_for(episodes_available: bool) -> list[dict[str, Any]]:
    if episodes_available:
        return TITLE_TYPE_OPTIONS
    return [option for option in TITLE_TYPE_OPTIONS if option["value"] != "tvEpisode"]


def active_filter_args(filters: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in filters.items() if value and key != "sort"}


def page_query_args(filters: dict[str, Any], page: int) -> dict[str, Any]:
    args = active_filter_args(filters)
    args["page"] = page
    return args


def view_query_args(filters: dict[str, Any], view: str) -> dict[str, Any]:
    args = active_filter_args(filters)
    args["view"] = view
    args["page"] = 1
    return args


def clear_compare_args(filters: dict[str, Any]) -> dict[str, Any]:
    args = active_filter_args(filters)
    args.pop("compare_ids", None)
    args["page"] = 1
    return args


def build_sort_controls(filters: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    current_by = filters.get("sort_by") or DEFAULT_SORT_BY
    current_dir = filters.get("sort_dir") or DEFAULT_SORT_DIR
    for value, label in SORT_COLUMN_OPTIONS:
        active = value == current_by
        next_dir = "desc" if active and current_dir == "asc" else "asc"
        args = active_filter_args(filters)
        args["sort_by"] = value
        args["sort_dir"] = next_dir
        args["page"] = 1
        controls.append(
            {
                "value": value,
                "label": label,
                "active": active,
                "dir": current_dir if active else "",
                "next_dir": next_dir,
                "url": url_for("index", **args),
            }
        )
    return controls


def find_titles(
    filters: dict[str, Any],
    page: int,
    akas_available: bool,
    episodes_available: bool,
) -> dict[str, Any]:
    start_time = time.perf_counter()
    quality_sql = quality_score_sql_parts(
        filters.get("quality_profile"), filters.get("score_mode")
    )
    where, params = title_filter_sql(
        filters,
        akas_available=akas_available,
        quality_score_expr=quality_sql["quality_score"],
    )
    total_count = count_titles(where, params)
    requested_all = filters.get("page_size") == "all"
    all_blocked = requested_all and total_count > SHOW_ALL_SAFE_LIMIT
    if all_blocked:
        filters["page_size"] = DEFAULT_PAGE_SIZE
        requested_all = False
        page = 1

    order_by = sort_order_sql(
        filters.get("sort_by") or DEFAULT_SORT_BY,
        filters.get("sort_dir") or DEFAULT_SORT_DIR,
        quality_score_expr=quality_sql["quality_score"],
    )
    page_size = total_count if requested_all else int(filters.get("page_size") or DEFAULT_PAGE_SIZE)
    offset = (page - 1) * page_size
    sql = f"""
        SELECT
            t.title_id,
            t.primary_title,
            t.premiered,
            t.type,
            t.genres,
            {episode_select_sql(episodes_available)}
            r.rating,
            r.votes,
            er.metascore,
            er.rotten_tomatoes_score,
            er.omdb_imdb_rating,
            pc.poster_url,
            pc.tmdb_id,
            {quality_select_sql(quality_sql, bool(filters.get("show_score_breakdown")))},
            wl.status AS watch_status
        FROM titles AS t
        {episode_join_sql(episodes_available)}
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        LEFT JOIN poster_cache AS pc ON pc.title_id = t.title_id
        {watchlist_join_sql()}
        WHERE {where}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
    """
    rows = get_db().execute(sql, (*params, page_size, offset)).fetchall()
    query_seconds = time.perf_counter() - start_time
    if query_seconds > SLOW_QUERY_SECONDS:
        LOGGER.warning(
            "Slow search query %.3fs filters=%s sort=%s/%s",
            query_seconds,
            {key: value for key, value in filters.items() if value},
            filters.get("sort_by"),
            filters.get("sort_dir"),
        )
    result_start = offset + 1 if rows else 0
    result_end = offset + len(rows)
    return {
        "rows": rows,
        "has_next": result_end < total_count and not requested_all,
        "total_count": total_count,
        "result_start": result_start,
        "result_end": result_end,
        "all_blocked": all_blocked,
        "page": page,
        "query_ms": round(query_seconds * 1000, 1),
    }


def count_titles(where: str, params: list[Any]) -> int:
    sql = f"""
        SELECT COUNT(*)
        FROM titles AS t
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        WHERE {where}
    """
    return int(get_db().execute(sql, params).fetchone()[0])


def watchlist_join_sql() -> str:
    user_id = current_user_id()
    if not user_id:
        return "LEFT JOIN watchlist AS wl ON 1 = 0"
    return f"LEFT JOIN watchlist AS wl ON wl.title_id = t.title_id AND wl.user_id = {user_id}"


def episode_select_sql(episodes_available: bool) -> str:
    if episodes_available:
        return """
            e.season_number,
            e.episode_number,
            s.primary_title AS series_title,
        """
    return """
            NULL AS season_number,
            NULL AS episode_number,
            NULL AS series_title,
        """


def episode_join_sql(episodes_available: bool) -> str:
    if not episodes_available:
        return ""
    return """
        LEFT JOIN episodes AS e ON e.episode_title_id = t.title_id
        LEFT JOIN titles AS s ON s.title_id = e.show_title_id
    """


def find_titles_for_export(
    filters: dict[str, Any], akas_available: bool, episodes_available: bool
) -> list[sqlite3.Row]:
    quality_sql = quality_score_sql_parts(
        filters.get("quality_profile"), filters.get("score_mode")
    )
    where, params = title_filter_sql(
        filters,
        akas_available=akas_available,
        quality_score_expr=quality_sql["quality_score"],
    )
    order_by = sort_order_sql(
        filters.get("sort_by") or DEFAULT_SORT_BY,
        filters.get("sort_dir") or DEFAULT_SORT_DIR,
        quality_score_expr=quality_sql["quality_score"],
    )
    sql = f"""
        SELECT
            t.title_id,
            t.primary_title,
            t.premiered,
            t.type,
            t.genres,
            {episode_select_sql(episodes_available)}
            r.rating,
            r.votes,
            er.metascore,
            er.rotten_tomatoes_score,
            er.omdb_imdb_rating,
            pc.poster_url,
            pc.tmdb_id,
            {quality_select_sql(quality_sql, bool(filters.get("show_score_breakdown")))},
            wl.status AS watch_status
        FROM titles AS t
        {episode_join_sql(episodes_available)}
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        LEFT JOIN poster_cache AS pc ON pc.title_id = t.title_id
        {watchlist_join_sql()}
        WHERE {where}
        ORDER BY {order_by}
    """
    return get_db().execute(sql, params).fetchall()


def find_watchlist(
    filters: dict[str, Any], page: int, akas_available: bool, episodes_available: bool
) -> tuple[list[sqlite3.Row], bool]:
    rows = query_watchlist(
        filters,
        limit=PER_PAGE + 1,
        offset=(page - 1) * PER_PAGE,
        akas_available=akas_available,
        episodes_available=episodes_available,
    )
    return rows[:PER_PAGE], len(rows) > PER_PAGE


def find_watchlist_for_export(
    filters: dict[str, Any], akas_available: bool, episodes_available: bool
) -> list[sqlite3.Row]:
    return query_watchlist(
        filters,
        limit=None,
        offset=0,
        akas_available=akas_available,
        episodes_available=episodes_available,
    )


def compare_ready(filters: dict[str, Any]) -> bool:
    return 2 <= len(filters.get("compare_ids") or []) <= 4


def find_compare_titles(
    filters: dict[str, Any], episodes_available: bool
) -> list[sqlite3.Row]:
    title_ids = filters.get("compare_ids") or []
    if not title_ids:
        return []
    quality_sql = quality_score_sql_parts(
        filters.get("quality_profile"), filters.get("score_mode")
    )
    placeholders = ", ".join("?" for _ in title_ids)
    ordering = " ".join(
        f"WHEN ? THEN {index}" for index, _title_id in enumerate(title_ids)
    )
    clauses = [f"t.title_id IN ({placeholders})"]
    params: list[Any] = [*title_ids]
    if not filters.get("include_adult"):
        clauses.append("COALESCE(t.is_adult, 0) = 0")
    sql = f"""
        SELECT
            t.title_id,
            t.primary_title,
            t.premiered,
            t.type,
            t.genres,
            {episode_select_sql(episodes_available)}
            r.rating,
            r.votes,
            er.metascore,
            er.rotten_tomatoes_score,
            er.omdb_imdb_rating,
            pc.poster_url,
            pc.tmdb_id,
            {quality_select_sql(quality_sql, True)},
            wl.status AS watch_status
        FROM titles AS t
        {episode_join_sql(episodes_available)}
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        LEFT JOIN poster_cache AS pc ON pc.title_id = t.title_id
        {watchlist_join_sql()}
        WHERE {" AND ".join(clauses)}
        ORDER BY CASE t.title_id {ordering} ELSE 99 END
    """
    return get_db().execute(sql, [*params, *title_ids]).fetchall()


def query_watchlist(
    filters: dict[str, Any],
    limit: int | None,
    offset: int,
    akas_available: bool,
    episodes_available: bool,
) -> list[sqlite3.Row]:
    quality_sql = quality_score_sql_parts(
        filters.get("quality_profile"), filters.get("score_mode")
    )
    where, params = title_filter_sql(
        filters,
        table_alias="t",
        akas_available=akas_available,
        quality_score_expr=quality_sql["quality_score"],
    )
    clauses = [where]
    clauses.append("wl.user_id = ?")
    params.append(current_user_id())
    if filters.get("status"):
        clauses.append("wl.status = ?")
        params.append(filters["status"])

    sql = f"""
        SELECT
            t.title_id,
            t.primary_title,
            t.premiered,
            t.type,
            t.genres,
            {episode_select_sql(episodes_available)}
            r.rating,
            r.votes,
            er.metascore,
            er.rotten_tomatoes_score,
            er.omdb_imdb_rating,
            pc.poster_url,
            pc.tmdb_id,
            {quality_select_sql(quality_sql, bool(filters.get("show_score_breakdown")))},
            wl.status,
            wl.notes,
            wl.added_at
        FROM watchlist AS wl
        INNER JOIN titles AS t ON t.title_id = wl.title_id
        {episode_join_sql(episodes_available)}
        LEFT JOIN ratings AS r ON r.title_id = t.title_id
        LEFT JOIN external_ratings AS er ON er.title_id = t.title_id
        LEFT JOIN poster_cache AS pc ON pc.title_id = t.title_id
        WHERE {" AND ".join(clauses)}
        ORDER BY wl.added_at DESC, t.primary_title COLLATE NOCASE ASC
    """
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return get_db().execute(sql, params).fetchall()


def title_filter_sql(
    filters: dict[str, Any],
    table_alias: str = "t",
    akas_available: bool = False,
    quality_score_expr: str | None = None,
) -> tuple[str, list[Any]]:
    t = table_alias
    quality_score_expr = quality_score_expr or quality_score_sql_parts(
        filters.get("quality_profile"), filters.get("score_mode")
    )["quality_score"]
    clauses = ["1 = 1"]
    params: list[Any] = []

    if not filters.get("include_adult"):
        clauses.append(f"COALESCE({t}.is_adult, 0) = 0")

    add_title_type_sql(clauses, params, filters.get("title_types") or [], t)

    if filters.get("start_year"):
        clauses.append(f"{t}.premiered >= ?")
        params.append(int(filters["start_year"]))
    if filters.get("end_year"):
        clauses.append(f"{t}.premiered <= ?")
        params.append(int(filters["end_year"]))
    if filters.get("genre"):
        clauses.append(f"(',' || IFNULL({t}.genres, '') || ',') LIKE ?")
        params.append(f"%,{filters['genre']},%")
    if filters.get("min_rating"):
        clauses.append("r.rating >= ?")
        params.append(float(filters["min_rating"]))
    if filters.get("max_rating"):
        clauses.append("r.rating <= ?")
        params.append(float(filters["max_rating"]))
    if filters.get("min_votes"):
        clauses.append("r.votes >= ?")
        params.append(int(filters["min_votes"]))
    if filters.get("max_votes"):
        clauses.append("r.votes <= ?")
        params.append(int(filters["max_votes"]))
    if filters.get("min_metascore"):
        clauses.append("er.metascore >= ?")
        params.append(int(filters["min_metascore"]))
    if filters.get("max_metascore"):
        clauses.append("er.metascore <= ?")
        params.append(int(filters["max_metascore"]))
    if filters.get("min_rotten_tomatoes"):
        clauses.append("er.rotten_tomatoes_score >= ?")
        params.append(int(filters["min_rotten_tomatoes"]))
    if filters.get("max_rotten_tomatoes"):
        clauses.append("er.rotten_tomatoes_score <= ?")
        params.append(int(filters["max_rotten_tomatoes"]))
    if filters.get("min_quality_score"):
        clauses.append(f"{quality_score_expr} >= ?")
        params.append(float(filters["min_quality_score"]))
    if filters.get("max_quality_score"):
        clauses.append(f"{quality_score_expr} <= ?")
        params.append(float(filters["max_quality_score"]))
    if filters.get("q"):
        clauses.append(f"{t}.primary_title COLLATE NOCASE LIKE ? ESCAPE '\\'")
        params.append(f"%{escape_like(filters['q'])}%")
    if filters.get("language_category") and akas_available:
        language_sql, language_params = language_filter_sql(
            filters["language_category"], t
        )
        clauses.append(language_sql)
        params.extend(language_params)

    return " AND ".join(clauses), params


def sort_order_sql(
    sort_by: str, sort_dir: str, quality_score_expr: str | None = None
) -> str:
    if sort_by == "quality_score":
        column = quality_score_expr or quality_score_sql_parts()["quality_score"]
    else:
        column = SORT_COLUMN_SQL.get(sort_by, SORT_COLUMN_SQL[DEFAULT_SORT_BY])
    direction = "ASC" if sort_dir == "asc" else "DESC"
    tie_breaker = "t.primary_title COLLATE NOCASE ASC"
    if sort_by == "title":
        tie_breaker = "t.premiered DESC"
    elif sort_by != "votes":
        tie_breaker = "r.votes DESC, t.primary_title COLLATE NOCASE ASC"
    return (
        f"CASE WHEN {column} IS NULL THEN 1 ELSE 0 END, "
        f"{column} {direction}, {tie_breaker}"
    )


def add_title_type_sql(
    clauses: list[str], params: list[Any], selected_values: list[str], table_alias: str
) -> None:
    db_types: list[str] = []
    include_documentary = False
    option_by_value = {option["value"]: option for option in TITLE_TYPE_OPTIONS}

    for value in selected_values:
        option = option_by_value.get(value)
        if not option:
            continue
        db_types.extend(option["types"])
        include_documentary = include_documentary or option["genre"] == "Documentary"

    terms: list[str] = []
    if db_types:
        placeholders = ", ".join("?" for _ in db_types)
        terms.append(f"{table_alias}.type IN ({placeholders})")
        params.extend(db_types)
    if include_documentary:
        terms.append(f"(',' || IFNULL({table_alias}.genres, '') || ',') LIKE ?")
        params.append("%,Documentary,%")
    if terms:
        clauses.append("(" + " OR ".join(terms) + ")")


def language_filter_sql(language_category: str, table_alias: str) -> tuple[str, list[Any]]:
    group = LANGUAGE_GROUPS[language_category]
    region_placeholders = ", ".join("?" for _ in group["regions"])
    language_placeholders = ", ".join("?" for _ in group["languages"])
    sql = f"""
        EXISTS (
            SELECT 1
            FROM akas AS a
            WHERE a.title_id = {table_alias}.title_id
              AND (
                a.region IN ({region_placeholders})
                OR a.language IN ({language_placeholders})
              )
        )
    """
    return sql, [*group["regions"], *group["languages"]]


def render_watchlist_csv(rows: list[sqlite3.Row]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "title_id",
            "title",
            "series",
            "season",
            "episode",
            "year",
            "type",
            "genres",
            "rating",
            "votes",
            "metascore",
            "rotten_tomatoes_score",
            "omdb_imdb_rating",
            "quality_score",
            "status",
            "notes",
            "added_at",
            "imdb_url",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["title_id"],
                row["primary_title"],
                row["series_title"],
                row["season_number"],
                row["episode_number"],
                row["premiered"],
                row["type"],
                row["genres"],
                row["rating"],
                row["votes"],
                row["metascore"],
                row["rotten_tomatoes_score"],
                row["omdb_imdb_rating"],
                row["quality_score"],
                row["status"],
                row["notes"],
                row["added_at"],
                imdb_url(row["title_id"]),
            ]
        )
    return output.getvalue()


def render_titles_csv(rows: list[sqlite3.Row], filters: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "title_id",
            "title",
            "series",
            "season",
            "episode",
            "year",
            "type",
            "genres",
            "rating",
            "votes",
            "metascore",
            "rotten_tomatoes_score",
            "omdb_imdb_rating",
            "quality_profile",
            "score_mode",
            "quality_score",
            "raw_imdb_score",
            "adjusted_imdb_score",
            "vote_confidence_score",
            "metascore_used",
            "rotten_tomatoes_score_used",
            "quality_missing_summary",
            "watchlist_status",
            "imdb_url",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["title_id"],
                row["primary_title"],
                row["series_title"],
                row["season_number"],
                row["episode_number"],
                row["premiered"],
                row["type"],
                row["genres"],
                row["rating"],
                row["votes"],
                row["metascore"],
                row["rotten_tomatoes_score"],
                row["omdb_imdb_rating"],
                filters.get("quality_profile"),
                filters.get("score_mode"),
                row["quality_score"],
                row["raw_imdb_score"],
                row["adjusted_imdb_score"],
                row["vote_confidence_score"],
                row["metascore_used"],
                row["rotten_tomatoes_score_used"],
                row["quality_missing_summary"],
                row["watch_status"],
                imdb_url(row["title_id"]),
            ]
        )
    return output.getvalue()


def clean_title_types(args: Any) -> list[str]:
    column_value = args.get("title_type_filter")
    if column_value in TITLE_TYPE_VALUES:
        return [column_value]
    raw_values = args.getlist("title_types") if hasattr(args, "getlist") else []
    if not raw_values and args.get("title_type"):
        raw_values = [args.get("title_type")]
    values = [value for value in raw_values if value in TITLE_TYPE_VALUES]
    return values or DEFAULT_TITLE_TYPE_FILTERS.copy()


def clean_title_ids(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        value = (value or "").strip()
        if (
            value.startswith("tt")
            and value[2:].isdigit()
            and value not in cleaned
            and len(cleaned) < 4
        ):
            cleaned.append(value)
    return cleaned


def clean_sort(args: Any) -> tuple[str, str]:
    sort_by = args.get("sort_by")
    sort_dir = args.get("sort_dir")
    valid_sort_columns = {value for value, _label in SORT_COLUMN_OPTIONS}
    if sort_by in valid_sort_columns:
        return sort_by, "asc" if sort_dir == "asc" else "desc"

    legacy = clean_choice(args.get("sort"), list(SORT_VALUES))
    legacy_map = {
        "quality_score_desc": ("quality_score", "desc"),
        "quality_score_asc": ("quality_score", "asc"),
        "votes_desc": ("votes", "desc"),
        "votes_asc": ("votes", "asc"),
        "rating_desc": ("rating", "desc"),
        "rating_asc": ("rating", "asc"),
        "year_desc": ("year", "desc"),
        "year_asc": ("year", "asc"),
        "title_asc": ("title", "asc"),
    }
    return legacy_map.get(legacy, (DEFAULT_SORT_BY, DEFAULT_SORT_DIR))


def clean_page_size(value: str | None) -> int | str:
    if (value or "").strip().lower() == "all":
        return "all"
    try:
        parsed = int(value or DEFAULT_PAGE_SIZE)
    except ValueError:
        return DEFAULT_PAGE_SIZE
    return parsed if parsed in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE


def language_filter_unavailable(filters: dict[str, Any], akas_available: bool) -> bool:
    return bool(filters.get("language_category") and not akas_available)


def is_adult_unlocked() -> bool:
    return bool(session.get("adult_unlocked"))


def title_type_label(value: str) -> str:
    labels = {option["value"]: option["label"] for option in TITLE_TYPE_OPTIONS}
    return labels.get(value, value)


def quality_profile_label(value: str | None) -> str:
    profile = QUALITY_PROFILES.get(
        value or DEFAULT_QUALITY_PROFILE,
        QUALITY_PROFILES[DEFAULT_QUALITY_PROFILE],
    )
    return profile["label"]


def quality_score_mode_label(value: str | None) -> str:
    return QUALITY_SCORE_MODES.get(
        value or DEFAULT_QUALITY_SCORE_MODE,
        QUALITY_SCORE_MODES[DEFAULT_QUALITY_SCORE_MODE],
    )


def episode_code(season_number: Any, episode_number: Any) -> str:
    parts: list[str] = []
    if season_number is not None:
        parts.append(f"S{int(season_number):02d}")
    if episode_number is not None:
        parts.append(f"E{int(episode_number):02d}")
    return "".join(parts)


def normalize_status(value: str | None) -> str:
    if value in WATCH_STATUS_VALUES:
        return value
    return "plan_to_watch"


def safe_next_url(default_endpoint: str = "index") -> str:
    fallback = url_for(default_endpoint)
    next_url = request.form.get("next") or request.args.get("next") or fallback
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return fallback
    return next_url


def clean_choice(value: str | None, options: list[str]) -> str:
    value = (value or "").strip()
    return value if value in options else ""


def clean_int_string(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = int(value)
    except ValueError:
        return ""
    return str(parsed)


def clean_float_string(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parsed = float(value)
    except ValueError:
        return ""
    return f"{parsed:g}"


def positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        return default
    return max(parsed, 1)


def escape_like(value: str) -> str:
    return value.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")


def imdb_url(title_id: str) -> str:
    return f"https://www.imdb.com/title/{title_id}/"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=env_flag("FLASK_DEBUG"))

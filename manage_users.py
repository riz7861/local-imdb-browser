from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import DEFAULT_DB_PATH, create_app, ensure_watchlist_schema, get_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage local IMDb browser users.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Path to imdb.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin = subparsers.add_parser("create-admin", help="Create or update an admin user")
    create_admin.add_argument("--username", default="admin", help="Admin username")
    create_admin.add_argument("--password", help="Admin password")

    args = parser.parse_args()
    if args.command == "create-admin":
        return create_admin_user(Path(args.db), args.username, args.password)
    return 0


def create_admin_user(db_path: Path, username: str, password: str | None) -> int:
    password = password or os.environ.get("IMDB_ADMIN_PASSWORD")
    if not password:
        first = getpass.getpass("Password: ")
        second = getpass.getpass("Confirm password: ")
        if first != second:
            raise SystemExit("Passwords did not match.")
        password = first
    if not password:
        raise SystemExit("Password cannot be empty.")

    app = create_app()
    app.config["DATABASE"] = db_path.expanduser().resolve()
    with app.app_context():
        ensure_watchlist_schema()
        db = get_db()
        db.execute(
            """
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash
            """,
            (username.strip(), generate_password_hash(password)),
        )
        db.commit()
    print(f"Admin user ready: {username.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

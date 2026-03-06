from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class DbConfig:
    path: str


def default_db_path() -> str:
    # Priority:
    # 1) DATABASE_PATH env var
    # 2) Render Disk mount (common mountPath: /var/data)
    # 3) Local instance/ folder (kept out of git)
    configured = (os.environ.get("DATABASE_PATH") or "").strip()
    if configured:
        return configured

    # If a persistent disk is mounted, use it automatically.
    if os.path.isdir("/var/data"):
        return os.path.join("/var/data", "resume_screening.db")

    return os.path.join("instance", "resume_screening.db")


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                email TEXT,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS screening_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_description TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS screening_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                name_guess TEXT,
                score REAL NOT NULL,
                similarity REAL NOT NULL,
                skill_overlap_count INTEGER NOT NULL,
                jd_skills_count INTEGER NOT NULL,
                matched_skills_json TEXT NOT NULL,
                skills_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES screening_runs(id) ON DELETE CASCADE
            );
            """
        )

        # Lightweight migration for existing DBs: ensure users.email exists.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        # Ensure email uniqueness if present (SQLite allows multiple NULLs).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email)"
        )


@contextmanager
def db_cursor(db_path: str) -> Iterator[sqlite3.Cursor]:
    with connect(db_path) as conn:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_user_by_username(db_path: str, username: str) -> Optional[sqlite3.Row]:
    with connect(db_path) as conn:
        normalized = (username or "").strip().lower()
        return conn.execute(
            "SELECT * FROM users WHERE lower(username) = ?",
            (normalized,),
        ).fetchone()


def get_user_by_email(db_path: str, email: str) -> Optional[sqlite3.Row]:
    with connect(db_path) as conn:
        normalized = (email or "").strip().lower()
        return conn.execute(
            "SELECT * FROM users WHERE lower(email) = ?",
            (normalized,),
        ).fetchone()


def get_user_by_id(db_path: str, user_id: int) -> Optional[sqlite3.Row]:
    with connect(db_path) as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

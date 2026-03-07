from __future__ import annotations

import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import default_db_path, init_db


def main() -> None:
    db_path = default_db_path()
    init_db(db_path)

    print(f"DB: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_events'"
        )
        exists = cur.fetchone() is not None
        print("auth_events exists:", exists)
        if not exists:
            return

        cur.execute("PRAGMA table_info(auth_events)")
        cols = [r[1] for r in cur.fetchall()]
        print("auth_events columns:", cols)

        cur.execute(
            "SELECT event_type, created_at, user_id FROM auth_events ORDER BY id DESC LIMIT 10"
        )
        rows = cur.fetchall()
        print(f"recent events: {len(rows)}")
        for r in rows:
            print(f"- {r['created_at']}  user_id={r['user_id']}  {r['event_type']}")


if __name__ == "__main__":
    main()

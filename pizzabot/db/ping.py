# ping.py — connectivity check: `python -m pizzabot.db.ping`
# Connects with DATABASE_URL, runs SELECT 1, prints "db ok". Exit 0 on success.

import os
import sys

import psycopg


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("db error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            row = conn.execute("SELECT 1").fetchone()
    except psycopg.Error as exc:
        # Class name only: the message can echo the connection string.
        print(f"db error: {type(exc).__name__}", file=sys.stderr)
        return 1
    if row != (1,):
        print(f"db error: unexpected result {row!r}", file=sys.stderr)
        return 1
    print("db ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

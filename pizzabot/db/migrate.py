# migrate.py — minimal SQL migration runner: `python -m pizzabot.db.migrate`
#
# Applies migrations/NNNN_name.sql in order, each in its own transaction, and
# records (version, checksum, applied_at) in schema_migrations. A session-level
# advisory lock serialises concurrent runs. An applied file whose checksum has
# changed (or that has disappeared) is an error: write a new migration instead.

from __future__ import annotations

import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from pizzabot.config import ROOT

MIGRATIONS_DIR = ROOT / "migrations"

# Arbitrary constant shared by every runner ("PIZZ" in ASCII).
LOCK_KEY = 0x50495A5A

_FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    checksum   text        NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Migrations on disk and in the database do not agree."""


@dataclass(frozen=True)
class Migration:
    version: str   # file stem, e.g. "0001_init"
    path: Path
    checksum: str  # sha256 of the file bytes

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Migration files sorted by number. Rejects badly named or duplicate numbers."""
    found: dict[str, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        m = _FILE_RE.match(path.name)
        if m is None:
            raise MigrationError(f"bad migration file name: {path.name} (want NNNN_name.sql)")
        number = m.group(1)
        if number in found:
            raise MigrationError(f"duplicate migration number {number}: "
                                 f"{found[number].path.name}, {path.name}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        found[number] = Migration(path.stem, path, checksum)
    return [found[n] for n in sorted(found)]


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied (empty = no-op).
    ``conn`` must be in autocommit mode; each migration gets its own transaction."""
    if not conn.autocommit:
        raise MigrationError("migrate() needs an autocommit connection")
    migrations = discover(directory)
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    try:
        conn.execute(_CREATE_TABLE)
        applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())

        on_disk = {m.version: m for m in migrations}
        for version, checksum in sorted(applied.items()):
            m = on_disk.get(version)
            if m is None:
                raise MigrationError(f"applied migration {version} is missing from {directory}")
            if m.checksum != checksum:
                raise MigrationError(f"checksum mismatch for applied migration {version}: "
                                     f"database {checksum[:12]}…, file {m.checksum[:12]}…")

        done = []
        for m in migrations:
            if m.version in applied:
                continue
            with conn.transaction():
                conn.execute(m.sql)
                conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                             (m.version, m.checksum))
            done.append(m.version)
        return done
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("migrate error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            done = migrate(conn)
    except MigrationError as exc:
        print(f"migrate error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # SQL errors carry no credentials; connection errors may echo the URL.
        detail = exc.diag.message_primary if exc.diag.message_primary else type(exc).__name__
        print(f"migrate error: {detail}", file=sys.stderr)
        return 1
    if done:
        for version in done:
            print(f"applied {version}")
    else:
        print("nothing to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())

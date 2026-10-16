# conftest.py — fixtures for tests against a real PostgreSQL.
#
# Skipped when DATABASE_URL is not set (e.g. on the dev laptop). Otherwise a
# throwaway database is created for the session, migrated, and dropped at the
# end; DATABASE_URL's own database is never touched. Each test runs in a
# transaction that is rolled back.

import os
import uuid
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import rows, sql  # noqa: E402
from psycopg.conninfo import make_conninfo  # noqa: E402

from pizzabot.db.migrate import migrate  # noqa: E402


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — PostgreSQL tests skipped")
    return url


@pytest.fixture(scope="session")
def fresh_db_url(admin_url):
    """URL of a new, empty database. Dropped after the session."""
    name = f"pizzabot_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(admin_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)")
                          .format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def first_migration(fresh_db_url) -> list[str]:
    """Versions applied by the first run on the empty database."""
    with psycopg.connect(fresh_db_url, autocommit=True) as conn:
        return migrate(conn)


@pytest.fixture(scope="session")
def db_url(fresh_db_url, first_migration) -> str:
    """URL of the migrated test database."""
    return fresh_db_url


@pytest.fixture
def conn(db_url):
    """Connection inside a transaction that is rolled back after the test.
    Use ``with conn.transaction():`` (a savepoint) around statements that are
    expected to fail."""
    with psycopg.connect(db_url) as c:
        try:
            yield c
        finally:
            c.rollback()


# --- seed helpers ------------------------------------------------------------------

def add_restaurant(conn, rid: str, *, tz: str = "Europe/Rome", vat: str = "0.10") -> None:
    conn.execute(
        "INSERT INTO restaurants (id, name, timezone, vat_rate) VALUES (%s, %s, %s, %s)",
        (rid, f"Pizzeria {rid}", tz, Decimal(vat)),
    )
    conn.execute(
        "INSERT INTO categories (restaurant_id, id, name) VALUES (%s, 'classiche', 'Pizze classiche')",
        (rid,),
    )


def order_row(rid: str, **overrides) -> dict:
    """A valid pickup order: 2× 6.00, IVA 10 % → vat 1.09, net 10.91."""
    row = {
        "order_id": uuid.uuid4().hex,
        "restaurant_id": rid,
        "public_code": None,
        "channel": "telegram",
        "customer_ref": "12345",
        "fulfilment": "pickup",
        "address": None,
        "kitchen_notes": None,
        "items_total_eur": Decimal("12.00"),
        "delivery_fee_eur": Decimal("0.00"),
        "total_eur": Decimal("12.00"),
        "vat_rate": Decimal("0.10"),
        "vat_eur": Decimal("1.09"),
        "net_eur": Decimal("10.91"),
        "status": "pending",
    }
    row.update(overrides)
    return row


def insert_order(conn, row: dict):
    cols = list(row)
    query = sql.SQL("INSERT INTO orders ({}) VALUES ({}) RETURNING *").format(
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    cur = conn.cursor(row_factory=rows.dict_row)
    return cur.execute(query, [row[c] for c in cols]).fetchone()

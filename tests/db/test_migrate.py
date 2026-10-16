# test_migrate.py — migration runner against a real PostgreSQL.

import shutil

import pytest

psycopg = pytest.importorskip("psycopg")

from pizzabot.db.migrate import MIGRATIONS_DIR, MigrationError, discover, migrate  # noqa: E402


EXPECTED_TABLES = {
    "schema_migrations", "restaurants", "categories", "menu_items", "option_groups",
    "options", "item_option_groups", "opening_hours", "slot_settings", "conversations",
    "order_counters", "orders", "order_lines", "order_line_options",
}


def test_first_run_applies_init_on_empty_db(first_migration, db_url):
    assert first_migration == [m.version for m in discover()]
    assert first_migration[0] == "0001_init"
    with psycopg.connect(db_url) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        recorded = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    assert EXPECTED_TABLES <= tables
    assert recorded == [(m.version, m.checksum) for m in discover()]


def test_second_run_is_noop(db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        assert migrate(conn) == []
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(discover())


def test_changed_checksum_is_detected(db_url, tmp_path):
    for f in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    edited = tmp_path / "0001_init.sql"
    edited.write_text(edited.read_text() + "\n-- edited after being applied\n")
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(MigrationError, match="checksum mismatch.*0001_init"):
            migrate(conn, tmp_path)


def test_missing_applied_file_is_detected(db_url, tmp_path):
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(MigrationError, match="0001_init is missing"):
            migrate(conn, tmp_path)


def test_new_migration_is_applied_in_its_own_transaction(db_url, tmp_path):
    for f in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "9998_ok.sql").write_text("CREATE TABLE t_ok (x int);")
    (tmp_path / "9999_broken.sql").write_text("CREATE TABLE t_broken (x int); SELECT 1/0;")
    with psycopg.connect(db_url, autocommit=True) as conn:
        try:
            with pytest.raises(psycopg.errors.DivisionByZero):
                migrate(conn, tmp_path)
            versions = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            assert "9998_ok" in versions and "9999_broken" not in versions
            assert conn.execute("SELECT to_regclass('t_broken')").fetchone()[0] is None
            # The lock was released: another run can proceed.
            assert conn.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                                ).fetchone()[0] == 0
        finally:
            conn.execute("DROP TABLE IF EXISTS t_ok")
            conn.execute("DELETE FROM schema_migrations WHERE version IN ('9998_ok', '9999_broken')")


def test_bad_file_name_is_rejected(tmp_path):
    (tmp_path / "1_init.sql").write_text("")
    with pytest.raises(MigrationError, match="bad migration file name"):
        discover(tmp_path)


def test_duplicate_number_is_rejected(tmp_path):
    (tmp_path / "0001_a.sql").write_text("")
    (tmp_path / "0001_b.sql").write_text("")
    with pytest.raises(MigrationError, match="duplicate migration number 0001"):
        discover(tmp_path)

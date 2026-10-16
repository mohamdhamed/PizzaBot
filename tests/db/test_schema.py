# test_schema.py — constraints of migrations/0001_init.sql against a real PostgreSQL.

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import errors  # noqa: E402

from tests.db.conftest import add_restaurant, insert_order, order_row  # noqa: E402


@pytest.fixture
def two_restaurants(conn):
    add_restaurant(conn, "r1")
    add_restaurant(conn, "r2")
    return conn


def add_item(conn, rid, item_id, category_id="classiche", price="6.00"):
    conn.execute(
        "INSERT INTO menu_items (restaurant_id, id, category_id, name, base_price_eur)"
        " VALUES (%s, %s, %s, %s, %s)",
        (rid, item_id, category_id, item_id.title(), Decimal(price)),
    )


# --- multi-tenancy ---------------------------------------------------------------

def test_same_menu_ids_in_two_restaurants(two_restaurants):
    add_item(two_restaurants, "r1", "margherita")
    add_item(two_restaurants, "r2", "margherita")


def test_cross_tenant_category_is_rejected(two_restaurants):
    conn = two_restaurants
    conn.execute("INSERT INTO categories (restaurant_id, id, name) VALUES ('r2', 'solo_r2', 'Solo R2')")
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        add_item(conn, "r1", "margherita", category_id="solo_r2")


def test_cross_tenant_option_group_is_rejected(two_restaurants):
    conn = two_restaurants
    add_item(conn, "r1", "margherita")
    conn.execute("INSERT INTO option_groups (restaurant_id, id, name) VALUES ('r2', 'dim', 'Dimensione')")
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO item_option_groups (restaurant_id, menu_item_id, option_group_id)"
                     " VALUES ('r1', 'margherita', 'dim')")


def test_cross_tenant_order_line_is_rejected(two_restaurants):
    conn = two_restaurants
    order = insert_order(conn, order_row("r2"))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        conn.execute(
            "INSERT INTO order_lines (restaurant_id, order_id, position, menu_item_id, dish_name,"
            " quantity, unit_price_eur, subtotal_eur)"
            " VALUES ('r1', %s, 0, 'margherita', 'Margherita', 2, 6.00, 12.00)",
            (order["order_id"],),
        )


# --- money & CHECK constraints ----------------------------------------------------

def test_negative_price_is_rejected(two_restaurants):
    with pytest.raises(errors.CheckViolation), two_restaurants.transaction():
        add_item(two_restaurants, "r1", "margherita", price="-0.01")


def test_negative_option_delta_is_allowed(two_restaurants):
    conn = two_restaurants
    conn.execute("INSERT INTO option_groups (restaurant_id, id, name) VALUES ('r1', 'dim', 'Dimensione')")
    conn.execute("INSERT INTO options (restaurant_id, id, option_group_id, name, price_delta_eur)"
                 " VALUES ('r1', 'baby', 'dim', 'Baby', -1.00)")


def test_allergen_outside_1_to_14_is_rejected(two_restaurants):
    conn = two_restaurants
    add_item(conn, "r1", "margherita")
    conn.execute("UPDATE menu_items SET allergens = '{1,7,14}' WHERE id = 'margherita'")
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE menu_items SET allergens = '{1,15}' WHERE id = 'margherita'")
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute("UPDATE menu_items SET allergens = '{0}' WHERE id = 'margherita'")


def test_net_plus_vat_must_equal_total(two_restaurants):
    with pytest.raises(errors.CheckViolation, match="orders_vat_split_check"), \
            two_restaurants.transaction():
        insert_order(two_restaurants, order_row("r1", net_eur=Decimal("10.90")))


def test_items_plus_fee_must_equal_total(two_restaurants):
    with pytest.raises(errors.CheckViolation, match="orders_total_check"), \
            two_restaurants.transaction():
        insert_order(two_restaurants, order_row("r1", items_total_eur=Decimal("11.00")))


def test_line_subtotal_must_match(two_restaurants):
    conn = two_restaurants
    order = insert_order(conn, order_row("r1"))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO order_lines (restaurant_id, order_id, position, menu_item_id, dish_name,"
            " quantity, unit_price_eur, subtotal_eur)"
            " VALUES ('r1', %s, 0, 'margherita', 'Margherita', 2, 6.00, 11.00)",
            (order["order_id"],),
        )


def test_zero_quantity_is_rejected(two_restaurants):
    conn = two_restaurants
    order = insert_order(conn, order_row("r1"))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO order_lines (restaurant_id, order_id, position, menu_item_id, dish_name,"
            " quantity, unit_price_eur, subtotal_eur)"
            " VALUES ('r1', %s, 0, 'margherita', 'Margherita', 0, 6.00, 0.00)",
            (order["order_id"],),
        )


def test_kitchen_notes_over_300_chars_is_rejected(two_restaurants):
    conn = two_restaurants
    insert_order(conn, order_row("r1", kitchen_notes="à" * 300))  # characters, not bytes
    with pytest.raises(errors.CheckViolation), conn.transaction():
        insert_order(conn, order_row("r1", kitchen_notes="x" * 301))


def test_kitchen_notes_control_chars_are_rejected(two_restaurants):
    conn = two_restaurants
    insert_order(conn, order_row("r1", kitchen_notes="senza cipolla\nben cotta"))
    with pytest.raises(errors.CheckViolation), conn.transaction():
        insert_order(conn, order_row("r1", kitchen_notes="senza\x1bcipolla"))


def test_invalid_status_is_rejected(two_restaurants):
    with pytest.raises(errors.CheckViolation), two_restaurants.transaction():
        insert_order(two_restaurants, order_row("r1", status="shipped"))


def test_status_channel_fulfilment_match_python_enums(conn):
    from pizzabot.order.schema import Channel, Fulfilment, OrderStatus

    add_restaurant(conn, "r1")
    for status in OrderStatus:
        insert_order(conn, order_row("r1", status=status.value))
    for channel in Channel:
        insert_order(conn, order_row("r1", channel=channel.value))
    for fulfilment in Fulfilment:
        delivery = fulfilment is Fulfilment.delivery
        insert_order(conn, order_row(
            "r1", fulfilment=fulfilment.value,
            address="Via Roma 1, 20900 Monza" if delivery else None,
        ))


def test_delivery_needs_address_and_pickup_has_no_fee(two_restaurants):
    conn = two_restaurants
    with pytest.raises(errors.CheckViolation, match="orders_delivery_address_check"), conn.transaction():
        insert_order(conn, order_row("r1", fulfilment="delivery"))
    with pytest.raises(errors.CheckViolation, match="orders_pickup_fee_check"), conn.transaction():
        insert_order(conn, order_row("r1", delivery_fee_eur=Decimal("2.00"),
                                     total_eur=Decimal("14.00"), vat_eur=Decimal("1.27"),
                                     net_eur=Decimal("12.73")))


def test_numeric_round_trips_to_decimal_exactly(two_restaurants):
    conn = two_restaurants
    for price in ("0.01", "0.10", "0.30", "12.35", "99999999.99"):
        add_item(conn, "r1", f"p{price.replace('.', '_')}", price=price)
        got = conn.execute("SELECT base_price_eur FROM menu_items WHERE restaurant_id = 'r1'"
                           " AND id = %s", (f"p{price.replace('.', '_')}",)).fetchone()[0]
        assert type(got) is Decimal
        assert got == Decimal(price) and str(got) == price
    order = insert_order(conn, order_row("r1"))
    assert order["vat_rate"] == Decimal("0.10") and str(order["vat_rate"]) == "0.10"
    assert (order["net_eur"], order["vat_eur"], order["total_eur"]) == \
        (Decimal("10.91"), Decimal("1.09"), Decimal("12.00"))


def test_vat_rate_defaults_from_restaurant(conn):
    add_restaurant(conn, "r1", vat="0.22")
    add_restaurant(conn, "r2", vat="0.10")
    o1 = insert_order(conn, order_row("r1", vat_rate=None, vat_eur=Decimal("2.16"),
                                      net_eur=Decimal("9.84")))
    o2 = insert_order(conn, order_row("r2", vat_rate=None))
    assert o1["vat_rate"] == Decimal("0.22")
    assert o2["vat_rate"] == Decimal("0.10")


# --- conversations -------------------------------------------------------------------

def _add_conversation(conn, rid, customer_ref="12345", channel="telegram"):
    conn.execute(
        "INSERT INTO conversations (restaurant_id, channel, customer_ref, state, state_data, cart,"
        " expires_at) VALUES (%s, %s, %s, 'item', %s, %s, now() + interval '2 hours')",
        (rid, channel, customer_ref, json.dumps({"category_id": "classiche"}),
         json.dumps([{"menu_item_id": "margherita", "option_ids": [], "quantity": 1}])),
    )


def test_duplicate_conversation_key_is_rejected(two_restaurants):
    conn = two_restaurants
    _add_conversation(conn, "r1")
    _add_conversation(conn, "r1", channel="whatsapp")   # other channel: fine
    _add_conversation(conn, "r2")                        # other restaurant: fine
    with pytest.raises(errors.UniqueViolation, match="conversations_key"), conn.transaction():
        _add_conversation(conn, "r1")


# --- public_code ------------------------------------------------------------------------

def test_duplicate_public_code_same_restaurant_same_day_is_rejected(two_restaurants):
    conn = two_restaurants
    insert_order(conn, order_row("r1", public_code="042"))
    insert_order(conn, order_row("r2", public_code="042"))  # other restaurant: fine
    with pytest.raises(errors.UniqueViolation, match="orders_public_code_key"), conn.transaction():
        insert_order(conn, order_row("r1", public_code="042"))


def test_same_public_code_on_another_business_day_is_allowed(two_restaurants):
    conn = two_restaurants
    day1 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    insert_order(conn, order_row("r1", public_code="042", created_at=day1))
    insert_order(conn, order_row("r1", public_code="042", created_at=day2))


def test_public_code_is_allocated_per_restaurant_per_day(two_restaurants):
    conn = two_restaurants
    day1 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    codes = [insert_order(conn, order_row(rid, created_at=at))["public_code"]
             for rid, at in [("r1", day1), ("r1", day1), ("r2", day1), ("r1", day1), ("r1", day2)]]
    assert codes == ["001", "002", "001", "003", "001"]


def test_business_date_uses_restaurant_timezone(conn):
    add_restaurant(conn, "rome")                       # Europe/Rome, UTC+2 in October
    add_restaurant(conn, "utc", tz="UTC")
    late = datetime(2026, 10, 15, 22, 30, tzinfo=timezone.utc)  # 00:30 on the 16th in Rome
    assert str(insert_order(conn, order_row("rome", created_at=late))["business_date"]) == "2026-10-16"
    assert str(insert_order(conn, order_row("utc", created_at=late))["business_date"]) == "2026-10-15"

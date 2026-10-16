# test_storage.py — pizzabot.storage against a real PostgreSQL.

import dataclasses
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from pizzabot.db.seed_demo import DEMO_VAT_RATE, demo_restaurant_settings, seed_demo  # noqa: E402
from pizzabot.flow.demo import demo_menu  # noqa: E402
from pizzabot.flow.machine import CartLine, Conversation, State  # noqa: E402
from pizzabot.flow.menu import Category, Menu, MenuItem, RestaurantSettings  # noqa: E402
from pizzabot.storage import (  # noqa: E402
    IDLE_TIMEOUT,
    ConfigError,
    load_conversation,
    load_flow_context,
    load_menu,
    save_conversation,
    save_restaurant,
)

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=ROME)


def test_seed_demo_is_idempotent(conn):
    assert seed_demo(conn) == "demo"
    assert seed_demo(conn) == "demo"
    counts = [conn.execute(f"SELECT count(*) FROM {t} WHERE restaurant_id = 'demo'").fetchone()[0]
              for t in ("categories", "menu_items", "options",
                        "item_option_groups", "opening_hours")]
    menu = demo_menu()
    assert counts == [len(menu.categories), len(menu.items),
                      sum(len(g.options) for g in menu.option_groups),
                      sum(len(i.option_group_ids) for i in menu.items), 7]
    rate = conn.execute("SELECT vat_rate FROM restaurants WHERE id = 'demo'").fetchone()[0]
    assert rate == DEMO_VAT_RATE


def test_menu_round_trips(conn):
    seed_demo(conn)
    ctx = load_flow_context(conn, "demo", NOW)
    assert ctx.menu == demo_menu()
    assert ctx.settings == demo_restaurant_settings()
    assert ctx.settings.vat_rate == Decimal("0.10")      # from the DB, not the 0.22 default
    # Option order and the standard option (= first of a required group).
    dim = ctx.menu.group("dim")
    assert [o.id for o in dim.options] == ["normale", "maxi", "baby"]
    assert ctx.menu.item("margherita").option_group_ids == ("dim", "impasto", "agg")
    assert ctx.menu.option("baby")[1].price_delta_eur == Decimal("-1.00")
    assert ctx.menu.option("bufala")[1].allergens == frozenset({7})
    assert ctx.menu.item("tiramisu").allergens == frozenset({1, 3, 7})
    assert ctx.menu.item("birra").age_restricted is True
    assert ctx.now is NOW and ctx.booked_slots == {}


def test_availability_round_trips(conn):
    seed_demo(conn)
    conn.execute("UPDATE menu_items SET available = false WHERE restaurant_id = 'demo' AND id = 'diavola'")
    conn.execute("UPDATE options SET available = false WHERE restaurant_id = 'demo' AND id = 'normale'")
    conn.execute("UPDATE categories SET active = false WHERE restaurant_id = 'demo' AND id = 'dolci'")
    menu = load_menu(conn, "demo")
    assert menu.item("diavola").available is False
    assert menu.option("normale")[1].available is False
    assert [o.id for o in menu.group("dim").available_options()] == ["maxi", "baby"]
    assert menu.category("dolci") is None and menu.item("tiramisu") is None


def test_unknown_restaurant_is_a_config_error(conn):
    with pytest.raises(ConfigError, match="unknown restaurant"):
        load_flow_context(conn, "nope", NOW)


def test_missing_slot_settings_is_a_config_error(conn):
    seed_demo(conn)
    conn.execute("DELETE FROM slot_settings WHERE restaurant_id = 'demo'")
    with pytest.raises(ConfigError, match="slot_settings"):
        load_flow_context(conn, "demo", NOW)


def test_restaurant_b_context_never_contains_a_items(conn):
    seed_demo(conn)
    b = RestaurantSettings(restaurant_id="b", name="Kebab B", vat_rate=Decimal("0.10"),
                           opening_hours={0: ((time(11), time(15)),)})
    save_restaurant(conn, b, Menu(categories=(Category("panini", "Panini"),),
                                  items=(MenuItem("panino", "panini", "Panino", Decimal("5.00")),)))
    ctx_b = load_flow_context(conn, "b", NOW)
    assert [i.id for i in ctx_b.menu.items] == ["panino"]
    assert ctx_b.menu.option_groups == ()
    assert ctx_b.settings.opening_hours == {0: ((time(11), time(15)),)}
    assert len(load_flow_context(conn, "demo", NOW).menu.items) == len(demo_menu().items)


# --- conversations ----------------------------------------------------------------------

def _conv(rid="demo", ref="111"):
    return Conversation(
        restaurant_id=rid, channel="telegram", customer_ref=ref, state=State.notes,
        data={"fulfilment": "pickup", "slot": "1030"},
        cart=[CartLine(menu_item_id="margherita", option_ids=["normale", "classico"], quantity=2)],
    )


def test_conversation_round_trips(conn):
    seed_demo(conn)
    save_conversation(conn, _conv(), NOW)
    loaded = load_conversation(conn, "demo", "telegram", "111", NOW + timedelta(minutes=5))
    assert loaded == _conv()
    expires = conn.execute("SELECT expires_at FROM conversations WHERE customer_ref = '111'"
                           ).fetchone()[0]
    assert expires == NOW + IDLE_TIMEOUT


def test_expired_conversation_comes_back_fresh(conn):
    seed_demo(conn)
    save_conversation(conn, _conv(), NOW)
    still = load_conversation(conn, "demo", "telegram", "111", NOW + IDLE_TIMEOUT - timedelta(seconds=1))
    assert still.state is State.notes
    fresh = load_conversation(conn, "demo", "telegram", "111", NOW + IDLE_TIMEOUT)
    assert fresh == Conversation(restaurant_id="demo", channel="telegram", customer_ref="111")


def test_missing_conversation_is_fresh_without_lock(conn):
    seed_demo(conn)
    conv = load_conversation(conn, "demo", "whatsapp", "+39333", NOW, for_update=False)
    assert conv.state is State.start and conv.cart == []
    assert conn.execute("SELECT count(*) FROM conversations WHERE restaurant_id = 'demo'"
                        ).fetchone()[0] == 0


def test_conversation_from_an_older_flow_comes_back_fresh(conn):
    seed_demo(conn)
    save_conversation(conn, _conv(), NOW)
    conn.execute("UPDATE conversations SET state = 'removed_state'")
    assert load_conversation(conn, "demo", "telegram", "111", NOW).state is State.start


def test_same_customer_on_two_restaurants_is_two_conversations(conn):
    seed_demo(conn)
    save_restaurant(conn, dataclasses.replace(demo_restaurant_settings(), restaurant_id="other"),
                    demo_menu())
    save_conversation(conn, _conv("demo"), NOW)
    assert load_conversation(conn, "other", "telegram", "111", NOW).state is State.start
    save_conversation(conn, _conv("other").model_copy(update={"state": State.slot}), NOW)
    assert load_conversation(conn, "demo", "telegram", "111", NOW).state is State.notes
    assert conn.execute("SELECT count(*) FROM conversations WHERE customer_ref = '111'"
                        " AND restaurant_id IN ('demo', 'other')").fetchone()[0] == 2

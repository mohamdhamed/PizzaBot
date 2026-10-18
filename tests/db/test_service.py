# test_service.py — process_event end to end against a real PostgreSQL.

import threading
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from pizzabot import service  # noqa: E402
from pizzabot.flow.machine import MSG_NO_SLOTS, MSG_STALE, Choice, State, Text  # noqa: E402
from pizzabot.storage import booked_slots, load_conversation, load_flow_context  # noqa: E402

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=ROME)
SLOT = datetime(2026, 10, 16, 10, 30, tzinfo=ROME)  # first slot: 10:00 + 20 min lead → 10:30


def send(conn, rid, event, ref="555", now=NOW):
    return service.process_event(conn, rid, "telegram", ref, event, now)


def to_confirm(conn, rid, ref="555", slot="slot:1030"):
    """start → Margherita ×2 → cart → pickup → slot → notes; ends on the summary."""
    for event in (Choice("order"), Choice("cat:classiche"), Choice("item:margherita"),
                  Choice("add:2"), Choice("cart"), Choice("proceed"), Choice("pickup")):
        send(conn, rid, event, ref)
    screen, _ = send(conn, rid, Choice(slot), ref)
    assert screen.accepts_text  # notes
    screen, placed = send(conn, rid, Text("senza cipolla, ben cotta"), ref)
    assert placed == [] and "confirm" in screen.option_ids()
    return screen


def count_orders(conn, rid):
    with conn.transaction():
        return conn.execute("SELECT count(*) FROM orders WHERE restaurant_id = %s",
                            (rid,)).fetchone()[0]


def conv_state(conn, rid, ref="555"):
    with conn.transaction():
        return load_conversation(conn, rid, "telegram", ref, NOW, for_update=False).state


# --- full flow ---------------------------------------------------------------------------

def test_full_flow_places_one_order(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)
    screen, placed = send(conn, rid, Choice("confirm"))

    assert [p.public_code for p in placed] == ["001"]
    assert screen.text.startswith("Ordine n. 001 inviato! ✅")
    assert conv_state(conn, rid) is State.done
    assert count_orders(conn, rid) == 1

    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    order = cur.execute("SELECT * FROM orders WHERE restaurant_id = %s", (rid,)).fetchone()
    assert order["order_id"] == placed[0].order.order_id
    assert order["public_code"] == "001"
    assert (order["items_total_eur"], order["delivery_fee_eur"], order["total_eur"]) == \
        (Decimal("12.00"), Decimal("0.00"), Decimal("12.00"))
    assert all(type(order[k]) is Decimal for k in
               ("items_total_eur", "delivery_fee_eur", "total_eur", "vat_rate", "vat_eur", "net_eur"))
    assert order["vat_rate"] == Decimal("0.10")
    assert (order["vat_eur"], order["net_eur"]) == (Decimal("1.09"), Decimal("10.91"))
    assert order["net_eur"] + order["vat_eur"] == order["total_eur"]
    assert order["fulfilment"] == "pickup" and order["status"] == "pending"
    assert order["slot_at"] == SLOT
    assert order["created_at"] == NOW
    assert order["kitchen_notes"] == "senza cipolla, ben cotta"

    menu = load_flow_context(conn, rid, NOW).menu
    margherita = menu.item("margherita")
    lines = cur.execute("SELECT * FROM order_lines WHERE order_id = %s ORDER BY position",
                        (order["order_id"],)).fetchall()
    assert [(l["menu_item_id"], l["dish_name"], l["quantity"], l["unit_price_eur"], l["subtotal_eur"])
            for l in lines] == [("margherita", margherita.name, 2, margherita.base_price_eur,
                                 Decimal("12.00"))]
    opts = cur.execute("SELECT option_id, name, price_delta_eur FROM order_line_options"
                       " WHERE order_line_id = %s ORDER BY position", (lines[0]["id"],)).fetchall()
    assert [(o["option_id"], o["name"], o["price_delta_eur"]) for o in opts] == [
        (o.id, o.name, o.price_delta_eur)
        for o in (menu.option("normale")[1], menu.option("classico")[1])
    ]
    conn.commit()


def test_public_codes_count_up_per_restaurant(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    codes = []
    for ref in ("a", "b"):
        to_confirm(conn, rid, ref)
        codes += [p.public_code for p in send(conn, rid, Choice("confirm"), ref)[1]]
    assert codes == ["001", "002"]


# --- double confirm ------------------------------------------------------------------------

def test_double_confirm_sequential_places_one_order(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)
    _, first = send(conn, rid, Choice("confirm"))
    screen, second = send(conn, rid, Choice("confirm"))
    assert len(first) == 1 and second == []
    assert screen.text.startswith(MSG_STALE) and "Ordine n. 001 inviato" in screen.text
    assert count_orders(conn, rid) == 1


def _concurrently(db_url, calls):
    """Run each (rid, event, ref) in its own thread and connection, started together."""
    barrier = threading.Barrier(len(calls))
    results, errors = [None] * len(calls), []

    def run(i, rid, event, ref):
        try:
            with psycopg.connect(db_url) as c:
                barrier.wait()
                results[i] = send(c, rid, event, ref)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i, *call)) for i, call in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    return results


def test_double_confirm_concurrent_places_one_order(idle_conn, seeded, db_url):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)
    results = _concurrently(db_url, [(rid, Choice("confirm"), "555")] * 2)
    placed = [p for _, ps in results for p in ps]
    assert [p.public_code for p in placed] == ["001"]
    loser = next(screen for screen, ps in results if not ps)
    assert loser.text.startswith(MSG_STALE)
    assert count_orders(conn, rid) == 1


# --- rollback ------------------------------------------------------------------------------

def test_failed_save_order_rolls_back_everything(idle_conn, seeded, monkeypatch):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)

    def boom(c, order):
        service_save(c, order)  # the order rows really are written…
        raise RuntimeError("kitchen printer on fire")  # …then the transaction fails

    service_save = service.save_order
    monkeypatch.setattr(service, "save_order", boom)
    with pytest.raises(RuntimeError, match="on fire"):
        send(conn, rid, Choice("confirm"))
    monkeypatch.undo()

    assert count_orders(conn, rid) == 0
    with conn.transaction():
        assert conn.execute("SELECT count(*) FROM order_counters WHERE restaurant_id = %s",
                            (rid,)).fetchone()[0] == 0
    assert conv_state(conn, rid) is State.confirm
    # Nothing was half-done: retrying works and gets the first code.
    _, placed = send(conn, rid, Choice("confirm"))
    assert [p.public_code for p in placed] == ["001"]


def test_rejects_connection_inside_a_transaction(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    conn.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="no open transaction"):
        send(conn, rid, Choice("order"))
    conn.rollback()


# --- snapshots ------------------------------------------------------------------------------

def test_menu_change_after_ordering_keeps_snapshot(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)
    send(conn, rid, Choice("confirm"))
    with conn.transaction():
        conn.execute("UPDATE menu_items SET base_price_eur = 9.90, name = 'Margherita DOP'"
                     " WHERE restaurant_id = %s AND id = 'margherita'", (rid,))
        conn.execute("UPDATE options SET name = 'Standard' WHERE restaurant_id = %s"
                     " AND id = 'normale'", (rid,))
    row = conn.execute(
        "SELECT l.dish_name, l.unit_price_eur, l.subtotal_eur, o.total_eur,"
        "       (SELECT array_agg(name ORDER BY position) FROM order_line_options"
        "         WHERE order_line_id = l.id)"
        "  FROM orders o JOIN order_lines l ON l.order_id = o.order_id"
        " WHERE o.restaurant_id = %s", (rid,)).fetchone()
    assert row == ("Margherita", Decimal("6.00"), Decimal("12.00"), Decimal("12.00"),
                   ["Normale", "Classico"])
    conn.rollback()


# --- tenant isolation ------------------------------------------------------------------------

def test_same_customer_on_two_restaurants(idle_conn, seeded):
    conn, a, b = idle_conn, seeded(), seeded()
    to_confirm(conn, a, ref="777")
    send(conn, b, Choice("order"), ref="777")
    assert conv_state(conn, a, "777") is State.confirm
    assert conv_state(conn, b, "777") is State.category
    _, placed = send(conn, a, Choice("confirm"), ref="777")
    assert placed[0].order.restaurant_id == a
    assert (count_orders(conn, a), count_orders(conn, b)) == (1, 0)


# --- booked slots ------------------------------------------------------------------------------

def test_placed_order_books_its_slot(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)
    send(conn, rid, Choice("confirm"))
    with conn.transaction():
        assert booked_slots(conn, rid, NOW.date()) == {SLOT: 1}
        assert booked_slots(conn, rid, NOW.date() + timedelta(days=1)) == {}
        assert load_flow_context(conn, rid, NOW).booked_slots == {SLOT: 1}
        conn.execute("UPDATE orders SET status = 'cancelled' WHERE restaurant_id = %s", (rid,))
        assert booked_slots(conn, rid, NOW.date()) == {}


def test_full_slot_is_not_offered_and_cannot_be_confirmed(idle_conn, seeded):
    conn, rid = idle_conn, seeded(max_orders_per_slot=1)
    to_confirm(conn, rid, ref="late")     # sits on the summary with 10:30
    to_confirm(conn, rid, ref="first")
    send(conn, rid, Choice("confirm"), ref="first")   # takes the only place at 10:30

    screen, placed = send(conn, rid, Choice("confirm"), ref="late")
    assert placed == []
    assert "non è più disponibile" in screen.text
    assert conv_state(conn, rid, "late") is State.slot
    assert "slot:1030" not in screen.option_ids() and "slot:1045" in screen.option_ids()
    assert count_orders(conn, rid) == 1


def test_concurrent_confirms_respect_slot_capacity(idle_conn, seeded, db_url):
    conn, rid = idle_conn, seeded(max_orders_per_slot=1)
    to_confirm(conn, rid, ref="x")
    to_confirm(conn, rid, ref="y")
    results = _concurrently(db_url, [(rid, Choice("confirm"), "x"), (rid, Choice("confirm"), "y")])
    assert sum(len(ps) for _, ps in results) == 1
    with conn.transaction():
        assert booked_slots(conn, rid, NOW.date()) == {SLOT: 1}


# --- "prima possibile" holds a slot (option A) -------------------------------------------------

# Open 09:00–10:30: at 10:00 (+20 min lead) the only slot left is 10:30.
LAST_SLOT_ONLY = {wd: ((time(9, 0), time(10, 30)),) for wd in range(7)}


def test_asap_order_takes_and_books_the_nearest_slot(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid, slot="slot:asap")
    _, (placed,) = send(conn, rid, Choice("confirm"))
    assert (placed.order.asap, placed.order.slot_at) == (True, SLOT)
    with conn.transaction():
        row = conn.execute("SELECT asap, slot_at FROM orders WHERE restaurant_id = %s",
                           (rid,)).fetchone()
        assert row == (True, SLOT)
        assert booked_slots(conn, rid, NOW.date()) == {SLOT: 1}


def test_full_nearest_slot_moves_asap_to_the_next(idle_conn, seeded):
    conn, rid = idle_conn, seeded(max_orders_per_slot=1)
    to_confirm(conn, rid, ref="first", slot="slot:1030")
    send(conn, rid, Choice("confirm"), ref="first")
    to_confirm(conn, rid, ref="asap", slot="slot:asap")
    _, (placed,) = send(conn, rid, Choice("confirm"), ref="asap")
    assert placed.order.slot_at == SLOT + timedelta(minutes=15)


def test_two_asap_orders_compete_for_the_last_slot(idle_conn, seeded, db_url):
    conn, rid = idle_conn, seeded(max_orders_per_slot=1, opening_hours=LAST_SLOT_ONLY)
    to_confirm(conn, rid, ref="x", slot="slot:asap")
    to_confirm(conn, rid, ref="y", slot="slot:asap")
    results = _concurrently(db_url, [(rid, Choice("confirm"), "x"), (rid, Choice("confirm"), "y")])
    placed = [p for _, ps in results for p in ps]
    assert len(placed) == 1 and placed[0].order.slot_at == SLOT
    loser_screen = next(screen for screen, ps in results if not ps)
    assert loser_screen.text.startswith(MSG_NO_SLOTS)
    assert "slot:asap" not in loser_screen.option_ids()
    loser = "y" if placed[0].order.customer_ref == "x" else "x"
    assert conv_state(conn, rid, loser) is State.slot
    assert count_orders(conn, rid) == 1


def test_no_capacity_left_places_no_order(idle_conn, seeded):
    conn, rid = idle_conn, seeded(max_orders_per_slot=1, opening_hours=LAST_SLOT_ONLY)
    to_confirm(conn, rid, ref="late", slot="slot:asap")
    to_confirm(conn, rid, ref="first", slot="slot:1030")
    send(conn, rid, Choice("confirm"), ref="first")
    screen, placed = send(conn, rid, Choice("confirm"), ref="late")
    assert placed == [] and screen.text.startswith(MSG_NO_SLOTS)
    assert count_orders(conn, rid) == 1


# --- restart (/start) ----------------------------------------------------------------------------

def test_restart_drops_the_stored_conversation(idle_conn, seeded):
    conn, rid = idle_conn, seeded()
    to_confirm(conn, rid)
    screen, placed = service.process_event(conn, rid, "telegram", "555", Text("/start"), NOW,
                                           restart=True)
    assert placed == [] and screen.option_ids() == {"order"}
    with conn.transaction():
        conv = load_conversation(conn, rid, "telegram", "555", NOW, for_update=False)
    assert conv.state is State.start and conv.cart == [] and conv.data == {}

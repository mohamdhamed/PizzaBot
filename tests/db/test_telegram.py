# test_telegram.py — the Telegram adapter + kitchen notification against a
# real PostgreSQL, with a fake Bot API (no network).

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from pizzabot import service  # noqa: E402
from pizzabot.channels.telegram.bot import MSG_RETRY, TelegramBot, db_connect  # noqa: E402
from pizzabot.channels.telegram.kitchen import KitchenNotifier, kitchen_message  # noqa: E402
from pizzabot.db import set_kitchen_chat  # noqa: E402
from pizzabot.flow.machine import MSG_STALE, Choice, Text  # noqa: E402
from pizzabot.storage import (  # noqa: E402
    count_stale_kitchen_orders, load_order, pending_kitchen_orders, set_kitchen_chat_id,
)
from tests.telegram_fakes import (  # noqa: E402
    FakeAPI, buttons, callback_update, network_down, text_update,
)

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=ROME)
KITCHEN = -1001112223334
CUSTOMER = 4242


class Clock:
    def __init__(self):
        self.mono = 1000.0

    def monotonic(self):
        return self.mono


def make_bot(db_url, rid, api=None, connect=None):
    api = api or FakeAPI()
    clock = Clock()
    notifier = KitchenNotifier(api, rid, clock=lambda: NOW, monotonic=clock.monotonic)
    bot = TelegramBot(api, connect or (lambda: db_connect(db_url)), rid, notifier=notifier,
                      clock=lambda: NOW, sleep=lambda s: None)
    return bot, api, clock


def with_kitchen(conn, rid, chat_id=KITCHEN):
    with conn.transaction():
        set_kitchen_chat_id(conn, rid, chat_id)


def kitchen_messages(api):
    return [p["text"] for p in api.sent("sendMessage", chat_id=KITCHEN)]


def orders(conn, rid):
    with conn.transaction():
        return conn.execute(
            "SELECT public_code, asap, slot_at, kitchen_notified_at FROM orders"
            " WHERE restaurant_id = %s ORDER BY public_code", (rid,)).fetchall()


class Chat:
    """Drives the bot like a Telegram client: presses buttons on the last
    message the bot sent or edited."""

    def __init__(self, bot, api):
        self.bot, self.api, self.uid, self.message_id = bot, api, 0, None

    def _next(self):
        self.uid += 1
        return self.uid

    def last(self):
        method, params = next((m, p) for m, p in reversed(self.api.calls)
                              if m in ("sendMessage", "editMessageText")
                              and p.get("chat_id") == CUSTOMER)
        return params

    def type(self, text):
        self.bot.handle_update(text_update(self._next(), text))
        self.message_id = self.api.next_message_id
        return self.last()

    def press(self, data):
        assert data in buttons(self.last()), (data, buttons(self.last()))
        before = self.api.next_message_id
        self.bot.handle_update(callback_update(self._next(), data, self.message_id))
        if self.api.next_message_id != before:  # edit failed → new message
            self.message_id = self.api.next_message_id
        return self.last()


# --- full flow through the adapter ----------------------------------------------------------

def test_full_flow_one_order_one_kitchen_message(idle_conn, seeded, db_url):
    rid = seeded()
    with_kitchen(idle_conn, rid)
    bot, api, _ = make_bot(db_url, rid)
    chat = Chat(bot, api)

    assert "Benvenuto" in chat.type("/start")["text"]
    for data in ("order", "cat:classiche", "item:margherita", "add:2", "cart", "proceed",
                 "pickup", "slot:asap"):
        chat.press(data)
    assert chat.last()["text"].startswith("Note per la cucina")
    summary = chat.type("senza cipolla")
    assert "prima possibile (entro 10:30)" in summary["text"]
    done = chat.press("confirm")

    assert done["text"].startswith("Ordine n. 001 inviato! ✅")
    assert buttons(done) == ["order"]
    # Every callback was answered.
    callbacks = sum(1 for m, _ in api.calls if m == "answerCallbackQuery")
    assert callbacks == 9
    (kitchen,) = kitchen_messages(api)
    assert "NUOVO ORDINE n. 001" in kitchen and "senza cipolla" in kitchen
    ((code, asap, slot_at, notified_at),) = orders(idle_conn, rid)
    assert (code, asap, slot_at, notified_at) == ("001", True, NOW + timedelta(minutes=30), NOW)

    # A second press of the old "Conferma" places nothing and sends nothing.
    bot.handle_update(callback_update(99, "confirm", chat.message_id))
    assert chat.last()["text"].startswith(MSG_STALE)
    assert len(orders(idle_conn, rid)) == 1 and len(kitchen_messages(api)) == 1
    bot.close()


def test_forged_callback_is_refused_by_the_machine(idle_conn, seeded, db_url):
    rid = seeded()
    bot, api, _ = make_bot(db_url, rid)
    bot.handle_update(text_update(1, "/start"))
    bot.handle_update(callback_update(2, "confirm", api.next_message_id))
    edit = api.sent("editMessageText")[0]
    assert edit["text"].startswith(MSG_STALE) and buttons(edit) == ["order"]
    assert orders(idle_conn, rid) == []
    bot.close()


def test_start_mid_order_starts_over(idle_conn, seeded, db_url):
    rid = seeded()
    bot, api, _ = make_bot(db_url, rid)
    chat = Chat(bot, api)
    chat.type("/start")
    for data in ("order", "cat:classiche", "item:margherita", "add"):
        chat.press(data)
    assert "🛒 Carrello" in str(chat.last()["reply_markup"])
    assert "Benvenuto" in chat.type("/start")["text"]
    chat.press("order")
    assert "Carrello" not in str(chat.last()["reply_markup"])  # cart dropped
    bot.close()


def test_photo_at_notes_step_is_not_a_note(idle_conn, seeded, db_url):
    rid = seeded()
    bot, api, _ = make_bot(db_url, rid)
    chat = Chat(bot, api)
    chat.type("/start")
    for data in ("order", "cat:classiche", "item:margherita", "add:2", "cart", "proceed",
                 "pickup", "slot:asap"):
        chat.press(data)
    bot.handle_update({"update_id": 50, "message": {
        "message_id": 50, "date": 1, "chat": {"id": CUSTOMER, "type": "private"},
        "from": {"id": CUSTOMER, "is_bot": False}, "photo": [{"file_id": "x"}],
        "caption": "ben cotta"}})
    assert chat.last()["text"].startswith("Scrivi un messaggio di testo")
    with idle_conn.transaction():
        data = idle_conn.execute("SELECT state, state_data FROM conversations"
                                 " WHERE restaurant_id = %s", (rid,)).fetchone()
    assert data[0] == "notes" and "notes" not in data[1]
    bot.close()


# --- kitchen message --------------------------------------------------------------------------

def place_delivery(conn, rid, ref="555"):
    send = lambda e: service.process_event(conn, rid, "telegram", ref, e, NOW)  # noqa: E731
    for e in (Choice("order"), Choice("cat:classiche"), Choice("item:margherita"),
              Choice("custom"), Choice("opt:maxi"), Choice("opt:classico"), Choice("opt:bufala"),
              Choice("next"), Choice("qty:2"), Choice("menu"), Choice("cat:bevande"),
              Choice("item:coca"), Choice("add"), Choice("cart"), Choice("proceed"),
              Choice("delivery"), Text("Via Roma 1, 20900 Monza"), Choice("slot:asap"),
              Text("senza cipolla & <ben> cotta")):
        send(e)
    _, placed = send(Choice("confirm"))
    return placed[0]


def test_kitchen_message_is_built_from_the_stored_order(idle_conn, seeded):
    rid = seeded()
    placed = place_delivery(idle_conn, rid)
    # The menu changes after the order: the kitchen still gets the snapshot.
    with idle_conn.transaction():
        idle_conn.execute("UPDATE menu_items SET name = 'Margherita DOP' WHERE restaurant_id = %s"
                          " AND id = 'margherita'", (rid,))
        stored = load_order(idle_conn, rid, placed.order.order_id)
    assert kitchen_message(stored).split("\n") == [
        "🔔 <b>NUOVO ORDINE n. 001</b>",
        "🛵 <b>CONSEGNA</b> — ⚡ Prima possibile (entro 10:30)",
        "",
        "<b>2×</b> Margherita",
        "      Maxi, Classico, Mozzarella di bufala",
        "<b>1×</b> Coca-Cola 33cl",
        "",
        "⚠️ <b>NOTE PER LA CUCINA:</b>",
        "senza cipolla &amp; &lt;ben&gt; cotta",
        "",
        "📍 <b>Indirizzo:</b> Via Roma 1, 20900 Monza",
        '📞 Telefono: non richiesto — <a href="tg://user?id=555">scrivi al cliente su Telegram</a>',
        "",
        "Consegna: 2,50 €",
        "💶 <b>Totale: 29,00 €</b> — pagamento alla consegna",
    ]


def test_kitchen_message_for_a_pickup_slot(idle_conn, seeded):
    rid = seeded()
    for e in (Choice("order"), Choice("cat:classiche"), Choice("item:diavola"), Choice("add"),
              Choice("item:margherita"), Choice("add"), Choice("cart"), Choice("proceed"),
              Choice("pickup"), Choice("slot:1100"), Choice("skip")):
        service.process_event(idle_conn, rid, "telegram", "9", e, NOW)
    _, (placed,) = service.process_event(idle_conn, rid, "telegram", "9", Choice("confirm"), NOW)
    with idle_conn.transaction():
        text = kitchen_message(load_order(idle_conn, rid, placed.order.order_id))
    assert "🏃 <b>RITIRO</b> — 🕒 ore 11:00" in text
    assert "NOTE" not in text and "Indirizzo" not in text and "Telefono" not in text
    assert text.endswith("💶 <b>Totale: 13,50 €</b> — pagamento al ritiro")


# --- notification failures ----------------------------------------------------------------------

def test_notify_failure_keeps_order_retries_and_marks_once(idle_conn, seeded, db_url):
    rid = seeded()
    with_kitchen(idle_conn, rid)
    bot, api, clock = make_bot(db_url, rid)
    api.fail = {"sendMessage": [network_down()]}         # Telegram unreachable once
    conn = bot.conn()
    placed = place_delivery(conn, rid)                    # committed by the service…
    bot._after_orders(1, [placed])                        # …then the adapter notifies
    attempts = lambda: len(api.sent("sendMessage", chat_id=KITCHEN))  # noqa: E731
    assert attempts() == 1
    ((code, _, _, notified),) = orders(idle_conn, rid)
    assert code == "001" and notified is None             # order kept, still pending

    bot.notifier.retry_pending(conn)                      # backoff (5 s) not over yet
    assert attempts() == 1
    clock.mono += 6
    bot.notifier.retry_pending(conn)
    assert attempts() == 2 and orders(idle_conn, rid)[0][3] == NOW

    clock.mono += 600
    bot.notifier.retry_pending(conn)                      # already notified: no duplicate
    bot.notifier.notify(conn, [placed.order.order_id])
    assert attempts() == 2
    with idle_conn.transaction():
        assert pending_kitchen_orders(idle_conn, rid, NOW) == []
    bot.close()


def test_backoff_grows_between_attempts(idle_conn, seeded, db_url):
    rid = seeded()
    with_kitchen(idle_conn, rid)
    bot, api, clock = make_bot(db_url, rid)
    conn = bot.conn()
    placed = place_delivery(conn, rid)
    api.fail = {"sendMessage": [network_down()] * 3}
    bot.notifier.notify(conn, [placed.order.order_id])    # fail 1 → next try in 5 s
    for wait, expect_attempt in ((4, False), (2, True), (9, False), (2, True)):
        before = len(api.sent("sendMessage", chat_id=KITCHEN))
        clock.mono += wait
        bot.notifier.retry_pending(conn)
        assert len(api.sent("sendMessage", chat_id=KITCHEN)) == before + expect_attempt
    bot.close()


def test_no_kitchen_chat_warns_and_keeps_orders_pending(idle_conn, seeded, db_url, caplog,
                                                        monkeypatch):
    rid = seeded()
    bot, api, clock = make_bot(db_url, rid)
    conn = bot.conn()
    placed = place_delivery(conn, rid)
    with caplog.at_level(logging.WARNING, logger="pizzabot.kitchen"):
        bot.notifier.notify(conn, [placed.order.order_id])
    assert f"python -m pizzabot.db.set_kitchen_chat {rid}" in caplog.text
    assert api.sent("sendMessage") == [] and orders(idle_conn, rid)[0][3] is None

    monkeypatch.setenv("DATABASE_URL", db_url)
    assert set_kitchen_chat.main([rid, str(KITCHEN)]) == 0
    clock.mono += 10
    bot.notifier.retry_pending(conn)
    assert len(kitchen_messages(api)) == 1 and orders(idle_conn, rid)[0][3] == NOW
    bot.close()


def test_supergroup_migration_updates_the_chat_and_sends(idle_conn, seeded, db_url):
    from pizzabot.channels.telegram.api import TelegramError
    rid = seeded()
    with_kitchen(idle_conn, rid, -4001)
    bot, api, _ = make_bot(db_url, rid)
    conn = bot.conn()
    placed = place_delivery(conn, rid)
    api.fail = {"sendMessage": [TelegramError("sendMessage", "Bad Request: group chat was "
                                              "upgraded to a supergroup chat", error_code=400,
                                              migrate_to_chat_id=KITCHEN)]}
    bot.notifier.notify(conn, [placed.order.order_id])
    assert len(kitchen_messages(api)) == 1
    with idle_conn.transaction():
        assert idle_conn.execute("SELECT kitchen_telegram_chat_id FROM restaurants WHERE id = %s",
                                 (rid,)).fetchone()[0] == KITCHEN
    bot.close()


def test_orders_of_earlier_days_are_not_resent(idle_conn, seeded):
    rid = seeded()
    place_delivery(idle_conn, rid)
    tomorrow = NOW + timedelta(days=1)
    with idle_conn.transaction():
        assert len(pending_kitchen_orders(idle_conn, rid, NOW)) == 1
        assert pending_kitchen_orders(idle_conn, rid, tomorrow) == []
        assert count_stale_kitchen_orders(idle_conn, rid, tomorrow) == 1
        idle_conn.execute("UPDATE orders SET status = 'cancelled' WHERE restaurant_id = %s", (rid,))
        assert pending_kitchen_orders(idle_conn, rid, NOW) == []


def test_set_kitchen_chat_cli_rejects_bad_input(seeded, db_url, monkeypatch, capsys):
    rid = seeded()
    monkeypatch.setenv("DATABASE_URL", db_url)
    assert set_kitchen_chat.main([rid]) == 2
    assert set_kitchen_chat.main([rid, "kitchen"]) == 2
    assert set_kitchen_chat.main(["nope", "-100123"]) == 1
    assert "unknown restaurant" in capsys.readouterr().err
    assert set_kitchen_chat.main([rid, "none"]) == 0


# --- DB session timeouts -------------------------------------------------------------------------

def test_db_session_has_lock_and_statement_timeouts(db_url):
    with db_connect(db_url) as c:
        assert c.execute("SHOW lock_timeout").fetchone()[0] == "2s"
        assert c.execute("SHOW statement_timeout").fetchone()[0] == "5s"


def test_lock_timeout_says_retry_and_the_loop_goes_on(idle_conn, seeded, db_url):
    rid = seeded()
    fast = lambda: psycopg.connect(db_url, options="-c lock_timeout=200")  # noqa: E731
    bot, api, _ = make_bot(db_url, rid, connect=fast)
    bot.handle_update(text_update(1, "/start"))
    with psycopg.connect(db_url) as holder:          # another session holds the row lock
        holder.execute("SELECT 1 FROM conversations WHERE restaurant_id = %s FOR UPDATE", (rid,))
        bot.handle_update(text_update(2, "ciao"))
        assert api.sent("sendMessage")[-1] == {"chat_id": CUSTOMER, "text": MSG_RETRY}
        holder.rollback()
    bot.handle_update(text_update(3, "ciao"))
    assert "Benvenuto" in api.sent("sendMessage")[-1]["text"]
    bot.close()

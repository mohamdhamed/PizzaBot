# test_telegram_bot.py — the adapter's update handling with a fake API and a
# fake service (no network, no database). Full flows against PostgreSQL are
# in tests/db/test_telegram.py.

import logging
from types import SimpleNamespace

import pytest
from psycopg import errors as pg_errors
from psycopg.pq import TransactionStatus

from pizzabot.channels.telegram import bot as botmod
from pizzabot.channels.telegram.bot import MSG_RETRY, TelegramBot
from pizzabot.channels.telegram.api import TelegramError
from pizzabot.flow import Choice, Other, Screen, ScreenOption, Text
from pizzabot.flow.machine import BACK, CANCEL
from tests.telegram_fakes import (
    GROUP, FakeAPI, buttons, callback_update, message_update, network_down, not_modified,
    text_update,
)

MENU = Screen("Cosa desideri?", (ScreenOption("cat:pizze", "Pizze"), BACK, CANCEL))
LONG = Screen("Pizze", tuple(ScreenOption(f"item:p{k}", f"Pizza {k} — 6,00 €") for k in range(12))
              + (CANCEL,))


class FakeConn:
    closed = broken = False
    info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def close(self):
        self.closed = True


class FakeNotifier:
    def __init__(self):
        self.notified, self.retries = [], 0

    def notify(self, conn, order_ids):
        self.notified.append(order_ids)

    def retry_pending(self, conn):
        self.retries += 1


class Service:
    """Stands in for pizzabot.service.process_event."""

    def __init__(self, screen=MENU):
        self.events, self.screen, self.raises, self.placed = [], screen, None, []

    def __call__(self, conn, rid, channel, ref, event, now, *, restart=False):
        self.events.append((rid, channel, ref, event, restart))
        if self.raises is not None:
            exc, self.raises = self.raises, None
            raise exc
        placed, self.placed = self.placed, []
        return self.screen, placed


@pytest.fixture
def setup(monkeypatch):
    api, svc = FakeAPI(), Service()
    monkeypatch.setattr(botmod.service, "process_event", svc)
    bot = TelegramBot(api, FakeConn, "demo", notifier=FakeNotifier(), sleep=lambda s: None)
    return bot, api, svc


# --- events -----------------------------------------------------------------------------

def test_text_becomes_text_event_for_that_customer(setup):
    bot, api, svc = setup
    bot.handle_update(text_update(1, "ciao"))
    assert svc.events == [("demo", "telegram", "4242", Text("ciao"), False)]
    (sent,) = api.sent("sendMessage")
    assert sent["chat_id"] == 4242 and sent["parse_mode"] == "HTML"
    assert buttons(sent) == ["cat:pizze", "back", "cancel"]


@pytest.mark.parametrize("text", ["/start", "/start@PizzaDemoBot", " /START ", "/start promo"])
def test_start_command_restarts_the_conversation(setup, text):
    bot, _, svc = setup
    bot.handle_update(text_update(1, text))
    assert svc.events[0][3:] == (Text("/start"), True)


@pytest.mark.parametrize("content", [
    {"photo": [{"file_id": "x", "width": 1, "height": 1}], "caption": "senza cipolla"},
    {"sticker": {"file_id": "x"}},
    {"voice": {"file_id": "x", "duration": 2}},
    {"location": {"latitude": 45.58, "longitude": 9.27}},
    {"contact": {"phone_number": "+39 333", "first_name": "M"}},
    {"text": "/help"},
])
def test_non_text_messages_become_other(setup, content):
    bot, api, svc = setup
    bot.handle_update(message_update(1, **content))
    assert svc.events[0][3] == Other()
    assert len(api.sent("sendMessage")) == 1


def test_groups_are_ignored_including_the_kitchen_group(setup):
    bot, api, svc = setup
    bot.handle_update(text_update(1, "ciao", chat=GROUP))
    bot.handle_update(text_update(2, "/start", chat={"id": -5, "type": "group"}))
    bot.handle_update(callback_update(3, "order", 9, chat=GROUP))
    assert svc.events == []
    assert api.methods() == ["answerCallbackQuery"]  # only the spinner is stopped


def test_bots_and_channel_posts_are_ignored(setup):
    bot, api, svc = setup
    bot.handle_update(text_update(1, "hi", user={"id": 7, "is_bot": True}))
    bot.handle_update({"update_id": 2, "channel_post": {"text": "x"}})
    assert svc.events == [] and api.calls == []


# --- callbacks ------------------------------------------------------------------------------

def test_callback_becomes_choice_and_edits_the_pressed_message(setup):
    bot, api, svc = setup
    bot.handle_update(callback_update(1, "order", message_id=55))
    assert svc.events[0][3] == Choice("order")
    assert api.methods() == ["editMessageText", "answerCallbackQuery"]
    edit = api.sent("editMessageText")[0]
    assert edit["message_id"] == 55 and buttons(edit) == ["cat:pizze", "back", "cancel"]
    assert api.sent("answerCallbackQuery") == [{"callback_query_id": "cb1"}]


def test_forged_callback_goes_to_the_machine_untouched(setup):
    bot, _, svc = setup
    bot.handle_update(callback_update(1, "confirm", message_id=55))
    assert svc.events[0][3] == Choice("confirm")  # the machine decides (MSG_STALE)


@pytest.mark.parametrize("data", ["x" * 65, "é" * 33, "", None, 12])
def test_oversized_or_missing_callback_data_is_rejected_but_answered(setup, data):
    bot, api, svc = setup
    update = callback_update(1, "x", 55)
    update["callback_query"]["data"] = data
    bot.handle_update(update)
    assert svc.events == []
    assert api.methods() == ["answerCallbackQuery"]


def test_callback_is_answered_even_when_processing_fails(setup):
    bot, api, svc = setup
    svc.raises = RuntimeError("boom")
    bot.handle_update(callback_update(1, "order", 55))
    assert api.sent("answerCallbackQuery") == [{"callback_query_id": "cb1", "text": MSG_RETRY}]


def test_callback_is_answered_even_when_edit_and_send_fail(setup):
    bot, api, _ = setup
    api.fail = {"editMessageText": [not_modified()], "sendMessage": [network_down()]}
    bot.handle_update(callback_update(1, "order", 55))
    assert api.methods() == ["editMessageText", "sendMessage", "answerCallbackQuery"]


def test_failing_answer_does_not_break_anything(setup):
    bot, api, _ = setup
    api.fail = {"answerCallbackQuery": [TelegramError("answerCallbackQuery",
                                                       "Bad Request: query is too old",
                                                       error_code=400)]}
    bot.handle_update(callback_update(1, "order", 55))
    bot.handle_update(text_update(2, "ciao"))
    assert api.methods()[-1] == "sendMessage"


# --- edit vs send ----------------------------------------------------------------------------

@pytest.mark.parametrize("error", [
    not_modified(),
    TelegramError("editMessageText", "Bad Request: message can't be edited", error_code=400),
    TelegramError("editMessageText", "Bad Request: message to edit not found", error_code=400),
])
def test_failed_edit_falls_back_to_a_new_message(setup, error):
    bot, api, _ = setup
    api.fail = {"editMessageText": [error]}
    bot.handle_update(callback_update(1, "order", 55))
    assert api.methods() == ["editMessageText", "sendMessage", "answerCallbackQuery"]
    assert buttons(api.sent("sendMessage")[0]) == ["cat:pizze", "back", "cancel"]
    assert bot.screens.get(4242).message_id == api.next_message_id  # pages follow the new one


def test_network_failure_on_edit_does_not_send_a_duplicate(setup):
    bot, api, _ = setup
    api.fail = {"editMessageText": [network_down("editMessageText")]}
    bot.handle_update(callback_update(1, "order", 55))
    assert "sendMessage" not in api.methods()
    assert api.sent("answerCallbackQuery")[0]["text"] == MSG_RETRY


# --- pagination --------------------------------------------------------------------------------

def test_page_buttons_rerender_the_last_screen_without_the_machine(setup):
    bot, api, svc = setup
    svc.screen = LONG
    bot.handle_update(text_update(1, "ciao"))
    message_id = api.next_message_id
    first = buttons(api.sent("sendMessage")[0])
    assert first[:8] == [f"item:p{k}" for k in range(8)] and "~pg:1" in first

    bot.handle_update(callback_update(2, "~pg:1", message_id))
    assert len(svc.events) == 1  # the machine was not asked
    second = buttons(api.sent("editMessageText")[0])
    assert second == ["item:p8", "item:p9", "item:p10", "item:p11", "~pg:0", "cancel"]

    bot.handle_update(callback_update(3, "item:p9", message_id))  # a real button again
    assert svc.events[-1][3] == Choice("item:p9")


def test_page_button_of_an_unknown_message_goes_to_the_machine(setup):
    bot, _, svc = setup
    bot.handle_update(callback_update(1, "~pg:1", message_id=999))  # e.g. after a restart
    assert svc.events[0][3] == Choice("~pg:1")  # → "scelta non più valida" + current screen


# --- database trouble ------------------------------------------------------------------------------

@pytest.mark.parametrize("exc", [pg_errors.LockNotAvailable("lock timeout"),
                                 pg_errors.QueryCanceled("statement timeout")])
def test_db_timeout_says_retry_and_keeps_the_connection(setup, exc):
    bot, api, svc = setup
    conn = bot.conn()
    svc.raises = exc
    bot.handle_update(text_update(1, "ciao"))
    assert api.sent("sendMessage") == [{"chat_id": 4242, "text": MSG_RETRY}]
    svc.raises = exc
    bot.handle_update(callback_update(2, "order", 55))
    assert api.sent("answerCallbackQuery")[-1]["text"] == MSG_RETRY
    assert bot.conn() is conn


def test_lost_connection_reconnects_on_next_update(setup):
    import psycopg
    bot, api, svc = setup
    first = bot.conn()
    svc.raises = psycopg.OperationalError("server closed the connection")
    bot.handle_update(text_update(1, "ciao"))
    assert api.sent("sendMessage")[0]["text"] == MSG_RETRY
    assert bot.conn() is not first


# --- the loop --------------------------------------------------------------------------------------

def test_bad_update_does_not_stop_the_loop(setup):
    bot, api, svc = setup
    api.updates = [[
        {"update_id": 10, "message": "not a dict"},
        {"update_id": 11},                                   # unknown kind
        text_update(12, "boom"),                              # service raises
        text_update(13, "ciao"),
    ]]
    original = svc.__call__

    def flaky(*args, **kw):
        if args[4] == Text("boom"):
            raise ValueError("bug")
        return original(*args, **kw)

    botmod.service.process_event = flaky
    assert bot.poll_once() == 4
    assert bot.offset == 14
    assert svc.events[-1][3] == Text("ciao")
    assert len(api.sent("sendMessage")) == 1


def test_run_survives_getupdates_failures_and_stops_cleanly(setup):
    bot, api, _ = setup
    api.fail = {"getUpdates": [network_down("getUpdates"),
                               TelegramError("getUpdates", "Conflict", error_code=409)]}
    api.updates = [[text_update(5, "ciao")]]
    rounds = iter([False, False, False, True])
    bot.run(lambda: next(rounds))
    assert bot.offset == 6
    # On stop the processed batch is confirmed so a restart does not replay it.
    assert api.calls[-1] == ("getUpdates", {"offset": 6, "timeout": 0, "limit": 1})
    assert bot.notifier.retries == 3


def test_revoked_token_stops_the_loop(setup):
    bot, api, _ = setup
    api.fail = {"getUpdates": [TelegramError("getUpdates", "Unauthorized", error_code=401)]}
    with pytest.raises(TelegramError):
        bot.run(lambda: False)


def test_placed_orders_go_to_the_kitchen_after_the_reply(setup):
    bot, api, svc = setup
    order = SimpleNamespace(order_id="o1")
    svc.placed = [SimpleNamespace(public_code="001", order=order)]
    bot.handle_update(callback_update(1, "confirm", 55))
    assert bot.notifier.notified == [["o1"]]
    assert api.methods() == ["editMessageText", "answerCallbackQuery"]


# --- privacy -----------------------------------------------------------------------------------------

def test_logs_hold_no_message_content(setup, caplog):
    bot, api, svc = setup
    secret_bits = ["Via Segreta 7", "senza cipolla", "+39 333 1234567", "Mario", "4242"]
    with caplog.at_level(logging.DEBUG):
        bot.handle_update(text_update(1, "Via Segreta 7, senza cipolla, +39 333 1234567"))
        svc.raises = ValueError("Failing row contains (Via Segreta 7, senza cipolla)")
        bot.handle_update(text_update(2, "Via Segreta 7"))
        bot.handle_update(message_update(3, contact={"phone_number": "+39 333 1234567"}))
    assert "update 1" in caplog.text and "restaurant=demo" in caplog.text
    for bit in secret_bits:
        assert bit not in caplog.text

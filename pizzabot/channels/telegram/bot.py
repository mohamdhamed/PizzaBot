"""Telegram long-polling adapter for ONE restaurant.

    getUpdates(offset) → for each update: event → service.process_event →
    render the screen (edit the pressed message, or send a new one) →
    after the commit, notify the kitchen.

Mapping (customer_ref = from.id, channel = telegram, private chats only):
- callback_query → Choice(data); ``~pg:<n>`` turns a page without the machine.
- text → Text(text); ``/start`` → the machine's start screen with a fresh
  conversation (``restart=True``); other ``/commands`` → Other.
- anything else (photo, sticker, voice, location…) → Other: the machine
  answers "Usa i pulsanti" and never takes it as an address or a note.
- group chats are ignored; the kitchen group only receives notifications.

Logs carry update ids, the restaurant id, event kinds, order codes and
error types — never message text, names, addresses, phones or notes.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import psycopg
from psycopg import errors as pg_errors
from psycopg.pq import TransactionStatus

from pizzabot import service
from pizzabot.channels.telegram.api import TelegramError
from pizzabot.channels.telegram.kitchen import KitchenNotifier, describe
from pizzabot.channels.telegram.render import callback_ok, parse_page, render_screen
from pizzabot.flow.machine import Choice, Event, Other, Screen, Text

log = logging.getLogger("pizzabot.telegram")

MSG_RETRY = "Riprova tra un attimo 🙏"

# Per DB session (set at connect): a customer waiting behind a stuck lock
# gets "Riprova" instead of a hanging bot.
LOCK_TIMEOUT_MS = 2000
STATEMENT_TIMEOUT_MS = 5000

POLL_TIMEOUT = 25  # seconds the server holds getUpdates open
ALLOWED_UPDATES = ["message", "callback_query"]

# lock_timeout → LockNotAvailable, statement_timeout → QueryCanceled; both
# are OperationalErrors, like a lost connection.
_TIMEOUTS = (pg_errors.LockNotAvailable, pg_errors.QueryCanceled)


def db_connect(url: str) -> psycopg.Connection:
    return psycopg.connect(
        url, connect_timeout=5,
        options=f"-c lock_timeout={LOCK_TIMEOUT_MS} -c statement_timeout={STATEMENT_TIMEOUT_MS}",
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Shown:
    message_id: int
    screen: Screen


class ScreenCache:
    """The last screen sent to each chat, so a page button can re-render it
    without asking the machine. Lost on restart: an old page button then goes
    to the machine, which answers "scelta non più valida" + the current screen."""

    def __init__(self, max_chats: int = 5000):
        self.max_chats = max_chats
        self._data: OrderedDict[int, Shown] = OrderedDict()

    def get(self, chat_id: int) -> Shown | None:
        shown = self._data.get(chat_id)
        if shown is not None:
            self._data.move_to_end(chat_id)
        return shown

    def put(self, chat_id: int, message_id: int, screen: Screen) -> None:
        self._data[chat_id] = Shown(message_id, screen)
        self._data.move_to_end(chat_id)
        while len(self._data) > self.max_chats:
            self._data.popitem(last=False)


def _is_command(text: str, name: str) -> bool:
    word = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return word.split("@", 1)[0].lower() == f"/{name}"


class TelegramBot:
    def __init__(self, api, connect: Callable[[], psycopg.Connection], restaurant_id: str, *,
                 notifier: KitchenNotifier | None = None,
                 clock: Callable[[], datetime] = utc_now,
                 sleep: Callable[[float], None] = time.sleep,
                 poll_timeout: int = POLL_TIMEOUT):
        self.api = api
        self.connect = connect
        self.restaurant_id = restaurant_id
        self.clock = clock
        self.sleep = sleep
        self.poll_timeout = poll_timeout
        self.notifier = notifier or KitchenNotifier(api, restaurant_id, clock=clock)
        self.screens = ScreenCache()
        self.offset: int | None = None
        self.polling = False   # True while blocked in getUpdates (see __main__)
        self._conn: psycopg.Connection | None = None

    # --- database session ------------------------------------------------------------

    def conn(self) -> psycopg.Connection:
        c = self._conn
        if c is None or c.closed or c.broken:
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
            self._conn = c = self.connect()
        elif c.info.transaction_status != TransactionStatus.IDLE:
            c.rollback()
        return c

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # --- loop ---------------------------------------------------------------------------

    def run(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        log.info("polling started restaurant=%s", self.restaurant_id)
        delay = 1.0
        try:
            while not should_stop():
                try:
                    self.poll_once()
                    delay = 1.0
                except KeyboardInterrupt:
                    break
                except TelegramError as exc:
                    if exc.error_code == 401:
                        raise  # token revoked: nothing to retry
                    if exc.error_code == 409:
                        log.error("getUpdates conflict: a webhook is set or another instance "
                                  "is polling with this token")
                    else:
                        log.error("getUpdates failed: %s", describe(exc))
                    self.sleep(delay)
                    delay = min(delay * 2, 60.0)
                except Exception as exc:  # noqa: BLE001 — keep polling
                    log.error("poll failed: %s", describe(exc))
                    self.sleep(delay)
                    delay = min(delay * 2, 60.0)
        finally:
            self._confirm_offset()
            log.info("polling stopped restaurant=%s", self.restaurant_id)

    def poll_once(self) -> int:
        """One getUpdates round. Returns the number of updates handled."""
        self._retry_kitchen()
        params = {"timeout": self.poll_timeout, "allowed_updates": ALLOWED_UPDATES}
        if self.offset is not None:
            params["offset"] = self.offset
        self.polling = True
        try:
            updates = self.api.call("getUpdates", params, timeout=self.poll_timeout + 10)
        finally:
            self.polling = False
        for update in updates or ():
            uid = update.get("update_id") if isinstance(update, dict) else None
            if isinstance(uid, int):
                self.offset = uid + 1  # never see it again, even if it fails below
            self.handle_update(update)
        return len(updates or ())

    def _confirm_offset(self) -> None:
        """Tell Telegram the last batch was handled, so a restart does not
        replay it (getUpdates confirms everything below ``offset``)."""
        if self.offset is None:
            return
        try:
            self.api.call("getUpdates", {"offset": self.offset, "timeout": 0, "limit": 1})
        except Exception as exc:  # noqa: BLE001
            log.warning("could not confirm the last updates: %s", describe(exc))

    def _retry_kitchen(self) -> None:
        try:
            self.notifier.retry_pending(self.conn())
        except Exception as exc:  # noqa: BLE001 — retry_pending never raises; conn() may
            log.error("kitchen retry skipped: %s", describe(exc))

    # --- one update ----------------------------------------------------------------------

    def handle_update(self, update) -> None:
        """Never raises: one bad update must not stop the loop."""
        uid = update.get("update_id") if isinstance(update, dict) else None
        try:
            if "callback_query" in update:
                self._on_callback(uid, update["callback_query"])
            elif "message" in update:
                self._on_message(uid, update["message"])
            else:
                log.info("update %s: ignored (unsupported update)", uid)
        except Exception as exc:  # noqa: BLE001
            self._log_failure(uid, exc)

    def _on_message(self, uid, msg: dict) -> None:
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        if chat.get("type") != "private":
            log.info("update %s: ignored (not a private chat)", uid)
            return
        if not isinstance(user.get("id"), int) or user.get("is_bot"):
            log.info("update %s: ignored (no human sender)", uid)
            return
        chat_id = chat.get("id", user["id"])

        text = msg.get("text")
        restart = False
        if isinstance(text, str) and _is_command(text, "start"):
            kind, event, restart = "start", Text("/start"), True
        elif isinstance(text, str) and not text.startswith("/"):
            kind, event = "text", Text(text)
        else:
            kind, event = "other", Other()   # photo, sticker, voice, unknown command…

        result = self._process(uid, kind, user["id"], event, restart)
        if result is None:
            self._send_plain(chat_id, MSG_RETRY)
            return
        screen, placed = result
        self.show(chat_id, screen)
        self._after_orders(uid, placed)

    def _on_callback(self, uid, cq: dict) -> None:
        toast: str | None = None
        placed = []
        try:
            user = cq.get("from") or {}
            msg = cq.get("message") or {}
            chat = msg.get("chat") or {}
            data = cq.get("data")
            if chat and chat.get("type") != "private":
                log.info("update %s: ignored callback (not a private chat)", uid)
                return
            if not isinstance(user.get("id"), int) or user.get("is_bot"):
                log.info("update %s: ignored callback (no human sender)", uid)
                return
            if not isinstance(data, str) or not callback_ok(data):
                log.warning("update %s: rejected callback (bad data)", uid)
                return
            chat_id = chat.get("id", user["id"])
            message_id = msg.get("message_id")

            page = parse_page(data)
            if page is not None:
                shown = self.screens.get(chat_id)
                if shown is not None and shown.message_id == message_id:
                    log.info("update %s: page %d restaurant=%s", uid, page, self.restaurant_id)
                    self.show(chat_id, shown.screen, page=page, edit=message_id)
                    return
                # Unknown page button (old message, restart): the machine
                # answers "scelta non più valida" with the current screen.

            result = self._process(uid, "callback", user["id"], Choice(data), False)
            if result is None:
                toast = MSG_RETRY
                return
            screen, placed = result
            self.show(chat_id, screen, edit=message_id)
        except Exception as exc:  # noqa: BLE001
            self._log_failure(uid, exc)
            toast = MSG_RETRY
        finally:
            # Always, so the button's spinner stops — even after an error.
            self._answer(uid, cq.get("id"), toast)
        self._after_orders(uid, placed)

    def _process(self, uid, kind: str, user_id: int, event: Event, restart: bool):
        """process_event, or None after a transient DB problem (timeout,
        lost connection) — the caller tells the customer to retry."""
        try:
            screen, placed = service.process_event(
                self.conn(), self.restaurant_id, "telegram", str(user_id), event, self.clock(),
                restart=restart)
        except psycopg.OperationalError as exc:
            log.warning("update %s: %s restaurant=%s db busy: %s",
                        uid, kind, self.restaurant_id, describe(exc))
            if not isinstance(exc, _TIMEOUTS):
                self.close()  # connection trouble: reconnect on the next update
            return None
        log.info("update %s: %s restaurant=%s", uid, kind, self.restaurant_id)
        return screen, placed

    def _after_orders(self, uid, placed) -> None:
        if not placed:
            return
        for p in placed:
            log.info("update %s: order %s placed restaurant=%s", uid, p.public_code,
                     self.restaurant_id)
        try:
            self.notifier.notify(self.conn(), [p.order.order_id for p in placed])
        except Exception as exc:  # noqa: BLE001 — the order is committed; retried later
            log.error("update %s: kitchen notification deferred: %s", uid, describe(exc))

    # --- Telegram output -------------------------------------------------------------------

    def show(self, chat_id: int, screen: Screen, *, page: int = 0, edit: int | None = None) -> None:
        """Edit the pressed message in place; send a new one if that fails
        (message too old, "not modified", inaccessible)."""
        r = render_screen(screen, page)
        params = {"chat_id": chat_id, "text": r.text, "parse_mode": "HTML"}
        if r.reply_markup is not None:
            params["reply_markup"] = r.reply_markup
        if edit is not None:
            try:
                self.api.call("editMessageText", {**params, "message_id": edit})
                self.screens.put(chat_id, edit, screen)
                return
            except TelegramError as exc:
                if exc.is_network:
                    raise
                log.info("edit failed, sending a new message: %s", exc.description[:80])
        sent = self.api.call("sendMessage", params)
        if isinstance(sent, dict) and isinstance(sent.get("message_id"), int):
            self.screens.put(chat_id, sent["message_id"], screen)

    def _send_plain(self, chat_id: int, text: str) -> None:
        try:
            self.api.call("sendMessage", {"chat_id": chat_id, "text": text})
        except Exception as exc:  # noqa: BLE001
            log.error("could not send the retry notice: %s", describe(exc))

    def _answer(self, uid, callback_id, toast: str | None) -> None:
        if not callback_id:
            return
        params = {"callback_query_id": callback_id}
        if toast:
            params["text"] = toast
        try:
            self.api.call("answerCallbackQuery", params)
        except Exception as exc:  # noqa: BLE001 — e.g. "query is too old"
            log.info("update %s: answerCallbackQuery failed: %s", uid, describe(exc))

    def _log_failure(self, uid, exc: BaseException) -> None:
        # Frames only (file, line, code): the exception message could echo
        # customer data (e.g. a constraint violation's failing row).
        frames = "".join(traceback.format_tb(exc.__traceback__))
        log.error("update %s failed restaurant=%s: %s\n%s", uid, self.restaurant_id,
                  describe(exc), frames.rstrip())

"""Kitchen notification: the confirmed order, read back from the database
(name / price snapshots), sent to the restaurant's Telegram group.

At-least-once: ``kitchen_notified_at`` is set only after Telegram accepted
every part of the message. A failure never touches the order; the order
stays pending and ``retry_pending`` (called from the polling loop) sends it
again with exponential backoff. If the process dies between Telegram's "ok"
and the UPDATE, the kitchen gets the message twice — never zero times.
"""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from typing import Callable

import psycopg

from pizzabot.channels.telegram.api import TelegramError
from pizzabot.channels.telegram.render import MAX_TEXT
from pizzabot.flow.machine import eur
from pizzabot.storage import (
    StoredOrder,
    count_stale_kitchen_orders,
    kitchen_chat_id,
    load_order,
    mark_kitchen_notified,
    pending_kitchen_orders,
    set_kitchen_chat_id,
)
log = logging.getLogger("pizzabot.kitchen")

WARN_EVERY = 600.0  # seconds between repeated configuration warnings


def _e(text: str) -> str:
    """Escape customer / menu text for parse_mode=HTML."""
    return html.escape(text, quote=False)


def kitchen_message(order: StoredOrder) -> str:
    """Italian, HTML. Everything comes from the stored snapshot."""
    out = [f"🔔 <b>NUOVO ORDINE n. {_e(order.public_code)}</b>"]

    mode = "🛵 <b>CONSEGNA</b>" if order.fulfilment == "delivery" else "🏃 <b>RITIRO</b>"
    if order.slot_at is None:
        when = "⚡ Prima possibile"
    elif order.asap:
        when = f"⚡ Prima possibile (entro {order.slot_at:%H:%M})"
    else:
        when = f"🕒 ore {order.slot_at:%H:%M}"
    out += [f"{mode} — {when}", ""]

    for line in order.lines:
        out.append(f"<b>{line.quantity}×</b> {_e(line.dish_name)}")
        if line.options:
            out.append("      " + ", ".join(_e(o.name) for o in line.options))

    if order.kitchen_notes:
        out += ["", "⚠️ <b>NOTE PER LA CUCINA:</b>", _e(order.kitchen_notes)]

    if order.fulfilment == "delivery":
        out += ["", f"📍 <b>Indirizzo:</b> {_e(order.address or '—')}"]
        # The guided flow does not ask for a phone number (yet): link the
        # customer's Telegram account instead.
        if order.channel == "telegram" and order.customer_ref.isdigit():
            out.append(f'📞 Telefono: non richiesto — '
                       f'<a href="tg://user?id={order.customer_ref}">scrivi al cliente su Telegram</a>')
        else:
            out.append("📞 Telefono: non richiesto")

    out.append("")
    if order.delivery_fee_eur:
        out.append(f"Consegna: {eur(order.delivery_fee_eur)}")
    pay = "alla consegna" if order.fulfilment == "delivery" else "al ritiro"
    out.append(f"💶 <b>Totale: {eur(order.total_eur)}</b> — pagamento {pay}")
    return "\n".join(out)


def split_message(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Split on line breaks into parts Telegram accepts. A kitchen message
    must never be cut short."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single huge line (cannot happen with 300-char notes)
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class KitchenNotifier:
    """Sends orders of one restaurant to its kitchen chat."""

    def __init__(self, api, restaurant_id: str, *,
                 clock: Callable[[], datetime],
                 monotonic: Callable[[], float] = time.monotonic,
                 base_delay: float = 5.0, max_delay: float = 300.0):
        self.api = api
        self.restaurant_id = restaurant_id
        self.clock = clock
        self.monotonic = monotonic
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._failures: dict[str, tuple[int, float]] = {}  # order_id → (failures, due)
        self._warned: dict[str, float] = {}

    # --- public -----------------------------------------------------------------

    def notify(self, conn: psycopg.Connection, order_ids: list[str]) -> None:
        """Right after the order's transaction committed. Never raises."""
        for order_id in order_ids:
            self._attempt(conn, order_id)

    def retry_pending(self, conn: psycopg.Connection) -> None:
        """Send today's not-yet-notified orders whose backoff has expired.
        Never raises."""
        try:
            now = self.clock()
            with conn.transaction():
                pending = pending_kitchen_orders(conn, self.restaurant_id, now)
                stale = count_stale_kitchen_orders(conn, self.restaurant_id, now)
        except Exception as exc:  # noqa: BLE001
            log.error("restaurant=%s kitchen retry: cannot read pending orders: %s",
                      self.restaurant_id, describe(exc))
            return
        if stale:
            self._warn("stale", "restaurant=%s: %d orders of earlier days were never sent "
                       "to the kitchen; they are not retried", self.restaurant_id, stale)
        due_now = self.monotonic()
        for order_id in pending:
            _, due = self._failures.get(order_id, (0, 0.0))
            if due <= due_now:
                self._attempt(conn, order_id)
        # Forget orders that are no longer pending (sent, cancelled, old).
        for order_id in list(self._failures):
            if order_id not in pending:
                del self._failures[order_id]

    # --- internals ---------------------------------------------------------------

    def _attempt(self, conn: psycopg.Connection, order_id: str) -> bool:
        rid = self.restaurant_id
        code = "?"
        try:
            with conn.transaction():
                chat_id = kitchen_chat_id(conn, rid)
                order = load_order(conn, rid, order_id)
            if order is None or order.kitchen_notified_at is not None:
                self._failures.pop(order_id, None)
                return True
            code = order.public_code
            if chat_id is None:
                self._warn("no_chat", "restaurant=%s has no kitchen_telegram_chat_id: orders stay "
                           "pending. Run: python -m pizzabot.db.set_kitchen_chat %s <chat_id>",
                           rid, rid)
                self._backoff(order_id)
                return False
            try:
                self._send(chat_id, kitchen_message(order))
            except TelegramError as exc:
                if exc.migrate_to_chat_id is None:
                    raise
                # The group was upgraded to a supergroup: Telegram tells us
                # the new id. Store it and send there.
                with conn.transaction():
                    set_kitchen_chat_id(conn, rid, int(exc.migrate_to_chat_id))
                log.warning("restaurant=%s: kitchen group became a supergroup; "
                            "kitchen_telegram_chat_id updated", rid)
                self._send(int(exc.migrate_to_chat_id), kitchen_message(order))
            with conn.transaction():
                mark_kitchen_notified(conn, rid, order_id, self.clock())
        except Exception as exc:  # noqa: BLE001 — the order is safe; retry later
            failures = self._backoff(order_id)
            log.error("restaurant=%s order=%s kitchen notification failed (attempt %d): %s",
                      rid, code, failures, describe(exc))
            return False
        self._failures.pop(order_id, None)
        log.info("restaurant=%s order=%s sent to the kitchen", rid, code)
        return True

    def _send(self, chat_id: int, text: str) -> None:
        for part in split_message(text):
            self.api.call("sendMessage", {"chat_id": chat_id, "text": part,
                                          "parse_mode": "HTML",
                                          "disable_web_page_preview": True})

    def _backoff(self, order_id: str) -> int:
        failures = self._failures.get(order_id, (0, 0.0))[0] + 1
        delay = min(self.base_delay * 2 ** (failures - 1), self.max_delay)
        self._failures[order_id] = (failures, self.monotonic() + delay)
        return failures

    def _warn(self, key: str, msg: str, *args) -> None:
        now = self.monotonic()
        last = self._warned.get(key)
        if last is None or now - last >= WARN_EVERY:
            self._warned[key] = now
            log.warning(msg, *args)


def describe(exc: BaseException) -> str:
    """Error summary safe for logs: type, SQLSTATE or Telegram description —
    never row data, message text or the token."""
    if isinstance(exc, TelegramError):
        return str(exc)
    if isinstance(exc, psycopg.Error):
        return f"{type(exc).__name__} (sqlstate {exc.sqlstate})" if exc.sqlstate else type(exc).__name__
    return type(exc).__name__

"""One customer event, end to end, in one database transaction.

    process_event(conn, restaurant_id, channel, customer_ref, event, now)
        -> (screen, placed orders)

lock + load conversation → load context → machine.handle → apply effects
(PlaceOrder → save_order, then the code goes into the done screen) → save
conversation → commit. Any error rolls back all of it: no half-saved order,
no advanced state. Notifying the kitchen is the caller's job, after the
commit, using the returned orders.

``now`` is the only clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.pq import TransactionStatus

from pizzabot.flow.machine import (
    Conversation,
    Event,
    PlaceOrder,
    Screen,
    State,
    handle,
    record_order_code,
    render,
)
from pizzabot.order.schema import Channel, Order
from pizzabot.storage import load_conversation, load_flow_context, save_conversation, save_order

# Namespace for pg_advisory_xact_lock(namespace, hashtext(restaurant_id)):
# taken before confirming, so slot capacity is checked and booked atomically
# per restaurant ("PZOR").
CONFIRM_LOCK_NS = 0x505A4F52


@dataclass(frozen=True)
class PlacedOrder:
    public_code: str
    order: Order


def process_event(
    conn_or_pool,
    restaurant_id: str,
    channel: Channel | str,
    customer_ref: str,
    event: Event,
    now: datetime,
    *,
    restart: bool = False,
) -> tuple[Screen, list[PlacedOrder]]:
    """``conn_or_pool`` is a psycopg Connection with no open transaction, or
    a pool with a ``connection()`` context manager (psycopg_pool style).

    ``restart`` drops the stored conversation (cart included) before the
    event, as if it had expired — e.g. Telegram's /start."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    args = (restaurant_id, Channel(channel), customer_ref, event, now, restart)
    if isinstance(conn_or_pool, psycopg.Connection):
        return _process(conn_or_pool, *args)
    with conn_or_pool.connection() as conn:
        return _process(conn, *args)


def _process(conn: psycopg.Connection, restaurant_id: str, channel: Channel, customer_ref: str,
             event: Event, now: datetime, restart: bool) -> tuple[Screen, list[PlacedOrder]]:
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("process_event needs a connection with no open transaction")

    placed: list[PlacedOrder] = []
    with conn.transaction():
        # Row lock: events of the same customer are serialised. A second
        # "Conferma" waits here and then sees state=done.
        conv = load_conversation(conn, restaurant_id, channel, customer_ref, now, for_update=True)
        if restart:
            conv = Conversation(restaurant_id=restaurant_id, channel=channel,
                                customer_ref=customer_ref)
        if conv.state is State.confirm:
            # Different customers confirming the same slot: serialise per
            # restaurant so the capacity check below sees committed bookings.
            conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                         (CONFIRM_LOCK_NS, restaurant_id))
        ctx = load_flow_context(conn, restaurant_id, now)
        result = handle(conv, event, ctx)
        conv, screen = result.conversation, result.screen
        for effect in result.effects:
            if isinstance(effect, PlaceOrder):
                if effect.order.restaurant_id != restaurant_id:
                    raise RuntimeError("order for another restaurant")
                placed.append(PlacedOrder(save_order(conn, effect.order), effect.order))
        if placed:
            # The database allocates the code; the machine renders it.
            conv = record_order_code(conv, placed[-1].public_code)
            screen = render(conv, ctx)
        save_conversation(conn, conv, now)
    return screen, placed

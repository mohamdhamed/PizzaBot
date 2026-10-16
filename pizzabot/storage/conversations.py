"""Conversation rows: one per (restaurant, channel, customer)."""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from pizzabot.flow.machine import Conversation
from pizzabot.order.schema import Channel

# A conversation idle for longer than this starts over (design-v1.md §2).
IDLE_TIMEOUT = timedelta(hours=2)


def load_conversation(
    conn: psycopg.Connection,
    restaurant_id: str,
    channel: Channel | str,
    customer_ref: str,
    now: datetime,
    for_update: bool = True,
) -> Conversation:
    """The stored conversation, or a fresh one if there is none, it expired,
    or it no longer fits the flow's model.

    With ``for_update`` the row is locked until the caller's transaction
    ends, so concurrent events of the same customer run one after another.
    A missing row is inserted first (already expired, i.e. fresh) so that
    there is always a row to lock — even for a customer's very first event.
    """
    channel = Channel(channel)
    key = (restaurant_id, channel.value, customer_ref)
    if for_update:
        conn.execute(
            "INSERT INTO conversations (restaurant_id, channel, customer_ref, updated_at,"
            " expires_at) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT ON CONSTRAINT conversations_key DO NOTHING",
            (*key, now, now),
        )
    row = conn.execute(
        "SELECT state, state_data, cart, expires_at FROM conversations"
        " WHERE restaurant_id = %s AND channel = %s AND customer_ref = %s"
        + (" FOR UPDATE" if for_update else ""),
        key,
    ).fetchone()

    fresh = Conversation(restaurant_id=restaurant_id, channel=channel, customer_ref=customer_ref)
    if row is None or row[3] <= now:
        return fresh
    state, data, cart, _ = row
    try:
        return Conversation(restaurant_id=restaurant_id, channel=channel,
                            customer_ref=customer_ref, state=state, data=data, cart=cart)
    except ValidationError:
        # Stored by an older version of the flow (e.g. a removed state).
        return fresh


def save_conversation(conn: psycopg.Connection, conv: Conversation, now: datetime) -> None:
    """Upsert; the conversation expires IDLE_TIMEOUT after this event."""
    dumped = conv.model_dump(mode="json")
    conn.execute(
        """
        INSERT INTO conversations (restaurant_id, channel, customer_ref, state, state_data,
                                   cart, updated_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ON CONSTRAINT conversations_key DO UPDATE SET
            state = EXCLUDED.state, state_data = EXCLUDED.state_data, cart = EXCLUDED.cart,
            updated_at = EXCLUDED.updated_at, expires_at = EXCLUDED.expires_at
        """,
        (conv.restaurant_id, conv.channel.value, conv.customer_ref, conv.state.value,
         Jsonb(dumped["data"]), Jsonb(dumped["cart"]), now, now + IDLE_TIMEOUT),
    )

"""Orders: insert the confirmed snapshot; count bookings per slot."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg

from pizzabot.order.schema import Order, OrderStatus
from pizzabot.storage.errors import ConfigError

# Statuses that no longer hold a place in a slot.
_FREES_SLOT = (OrderStatus.cancelled.value,)


def save_order(conn: psycopg.Connection, order: Order) -> str:
    """Insert the order with its lines and options (name + price snapshots)
    in one savepoint. The database allocates public_code; it is returned."""
    if order.vat_rate != order.vat_rate.quantize(Decimal("0.01")):
        # orders.vat_rate is NUMERIC(5,2): refuse rather than round silently.
        raise ValueError(f"vat_rate {order.vat_rate} has more than 2 decimal places")
    with conn.transaction():
        (public_code,) = conn.execute(
            """
            INSERT INTO orders (order_id, restaurant_id, channel, customer_ref, fulfilment,
                                address, slot_at, kitchen_notes, items_total_eur,
                                delivery_fee_eur, total_eur, vat_rate, vat_eur, net_eur,
                                status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING public_code
            """,
            (order.order_id, order.restaurant_id, order.channel.value, order.customer_ref,
             order.fulfilment.value, order.address, order.slot_at, order.kitchen_notes,
             order.items_total_eur, order.delivery_fee_eur, order.total_eur, order.vat_rate,
             order.vat_eur, order.net_eur, order.status.value, order.created_at),
        ).fetchone()
        for position, item in enumerate(order.items):
            (line_id,) = conn.execute(
                """
                INSERT INTO order_lines (restaurant_id, order_id, position, menu_item_id,
                                         dish_name, quantity, unit_price_eur, subtotal_eur)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (order.restaurant_id, order.order_id, position, item.menu_item_id,
                 item.dish_name, item.quantity, item.unit_price_eur, item.subtotal_eur),
            ).fetchone()
            if item.options:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO order_line_options (restaurant_id, order_line_id, position,"
                        " option_id, name, price_delta_eur) VALUES (%s, %s, %s, %s, %s, %s)",
                        [(order.restaurant_id, line_id, pos, o.option_id, o.name, o.price_delta_eur)
                         for pos, o in enumerate(item.options)],
                    )
    return public_code


def booked_slots(conn: psycopg.Connection, restaurant_id: str, day: date) -> dict[datetime, int]:
    """Orders per slot on ``day`` (the restaurant's local date), cancelled
    orders excluded. Keys are aware datetimes in the restaurant's timezone,
    as produced by pizzabot.flow.slots. "Prima possibile" orders have no slot
    and are not counted."""
    row = conn.execute("SELECT timezone FROM restaurants WHERE id = %s", (restaurant_id,)).fetchone()
    if row is None:
        raise ConfigError(f"unknown restaurant {restaurant_id!r}")
    tz = ZoneInfo(row[0])
    start = datetime.combine(day, time(0), tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tz)
    counts: Counter[datetime] = Counter()
    for slot_at, n in conn.execute(
        """
        SELECT slot_at, count(*) FROM orders
         WHERE restaurant_id = %s AND slot_at >= %s AND slot_at < %s
           AND status <> ALL(%s)
         GROUP BY slot_at
        """,
        (restaurant_id, start, end, list(_FREES_SLOT)),
    ):
        counts[slot_at.astimezone(tz)] += n
    return dict(counts)

"""Orders: insert the confirmed snapshot; count bookings per slot."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
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
                                address, slot_at, asap, kitchen_notes, items_total_eur,
                                delivery_fee_eur, total_eur, vat_rate, vat_eur, net_eur,
                                status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING public_code
            """,
            (order.order_id, order.restaurant_id, order.channel.value, order.customer_ref,
             order.fulfilment.value, order.address, order.slot_at, order.asap,
             order.kitchen_notes,
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
    as produced by pizzabot.flow.slots. "Prima possibile" orders hold the
    slot they were assigned at confirm, so they count like any other."""
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


# --- kitchen notification -------------------------------------------------------------

@dataclass(frozen=True)
class StoredOption:
    name: str
    price_delta_eur: Decimal


@dataclass(frozen=True)
class StoredLine:
    dish_name: str
    quantity: int
    unit_price_eur: Decimal
    subtotal_eur: Decimal
    options: tuple[StoredOption, ...]


@dataclass(frozen=True)
class StoredOrder:
    """An order as persisted (name and price snapshots), for the kitchen."""
    order_id: str
    restaurant_id: str
    public_code: str
    business_date: date
    channel: str
    customer_ref: str
    fulfilment: str
    address: str | None
    slot_at: datetime | None   # in the restaurant's timezone
    asap: bool
    kitchen_notes: str | None
    items_total_eur: Decimal
    delivery_fee_eur: Decimal
    total_eur: Decimal
    status: str
    created_at: datetime
    kitchen_notified_at: datetime | None
    lines: tuple[StoredLine, ...]


def load_order(conn: psycopg.Connection, restaurant_id: str, order_id: str) -> StoredOrder | None:
    row = conn.execute(
        """
        SELECT o.order_id, o.restaurant_id, o.public_code, o.business_date, o.channel,
               o.customer_ref, o.fulfilment, o.address, o.slot_at, o.asap, o.kitchen_notes,
               o.items_total_eur, o.delivery_fee_eur, o.total_eur, o.status, o.created_at,
               o.kitchen_notified_at, r.timezone
          FROM orders o JOIN restaurants r ON r.id = o.restaurant_id
         WHERE o.restaurant_id = %s AND o.order_id = %s
        """,
        (restaurant_id, order_id),
    ).fetchone()
    if row is None:
        return None
    *fields, tz = row
    lines: list[StoredLine] = []
    options_of: dict[int, list[StoredOption]] = {}
    for line_id, name, delta in conn.execute(
        """
        SELECT lo.order_line_id, lo.name, lo.price_delta_eur
          FROM order_line_options lo JOIN order_lines l ON l.id = lo.order_line_id
         WHERE l.restaurant_id = %s AND l.order_id = %s
         ORDER BY lo.order_line_id, lo.position
        """,
        (restaurant_id, order_id),
    ):
        options_of.setdefault(line_id, []).append(StoredOption(name, delta))
    for line_id, dish, qty, unit, subtotal in conn.execute(
        "SELECT id, dish_name, quantity, unit_price_eur, subtotal_eur FROM order_lines"
        " WHERE restaurant_id = %s AND order_id = %s ORDER BY position",
        (restaurant_id, order_id),
    ):
        lines.append(StoredLine(dish, qty, unit, subtotal, tuple(options_of.get(line_id, ()))))
    order = StoredOrder(*fields, lines=tuple(lines))
    if order.slot_at is not None:
        order = replace(order, slot_at=order.slot_at.astimezone(ZoneInfo(tz)))
    return order


def pending_kitchen_orders(conn: psycopg.Connection, restaurant_id: str,
                           now: datetime) -> list[str]:
    """Ids of today's orders (restaurant's business day of ``now``) the
    kitchen has not been told about yet, oldest first. Cancelled orders are
    left out."""
    return [r[0] for r in conn.execute(
        """
        SELECT o.order_id FROM orders o JOIN restaurants r ON r.id = o.restaurant_id
         WHERE o.restaurant_id = %s AND o.kitchen_notified_at IS NULL
           AND o.business_date = (%s::timestamptz AT TIME ZONE r.timezone)::date
           AND o.status <> 'cancelled'
         ORDER BY o.created_at, o.public_code
        """,
        (restaurant_id, now),
    )]


def count_stale_kitchen_orders(conn: psycopg.Connection, restaurant_id: str,
                               now: datetime) -> int:
    """Never-notified orders of earlier business days: no longer sent (orders
    are same-day only), only reported."""
    return conn.execute(
        """
        SELECT count(*) FROM orders o JOIN restaurants r ON r.id = o.restaurant_id
         WHERE o.restaurant_id = %s AND o.kitchen_notified_at IS NULL
           AND o.business_date < (%s::timestamptz AT TIME ZONE r.timezone)::date
           AND o.status <> 'cancelled'
        """,
        (restaurant_id, now),
    ).fetchone()[0]


def mark_kitchen_notified(conn: psycopg.Connection, restaurant_id: str, order_id: str,
                          now: datetime) -> bool:
    """Record that Telegram confirmed the kitchen message. Only the first call
    sets the time; returns whether this call did."""
    cur = conn.execute(
        "UPDATE orders SET kitchen_notified_at = %s"
        " WHERE restaurant_id = %s AND order_id = %s AND kitchen_notified_at IS NULL",
        (now, restaurant_id, order_id),
    )
    return cur.rowcount == 1


def kitchen_chat_id(conn: psycopg.Connection, restaurant_id: str) -> int | None:
    row = conn.execute("SELECT kitchen_telegram_chat_id FROM restaurants WHERE id = %s",
                       (restaurant_id,)).fetchone()
    if row is None:
        raise ConfigError(f"unknown restaurant {restaurant_id!r}")
    return row[0]


def set_kitchen_chat_id(conn: psycopg.Connection, restaurant_id: str, chat_id: int | None) -> None:
    cur = conn.execute("UPDATE restaurants SET kitchen_telegram_chat_id = %s WHERE id = %s",
                       (chat_id, restaurant_id))
    if cur.rowcount != 1:
        raise ConfigError(f"unknown restaurant {restaurant_id!r}")

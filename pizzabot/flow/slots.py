"""Opening hours and pickup / delivery time slots — pure functions of ``now``.

[A9] Orders are "as soon as possible" (only while open; it takes the nearest
slot with capacity) or for a slot later the same day. Ranges that cross
midnight are not supported in v1.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping
from zoneinfo import ZoneInfo

from pizzabot.flow.menu import RestaurantSettings


def _local(settings: RestaurantSettings, now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(settings.timezone))


def _ranges_today(settings: RestaurantSettings, local_now: datetime) -> list[tuple[datetime, datetime]]:
    tz = local_now.tzinfo
    day = local_now.date()
    return [
        (datetime.combine(day, opens, tz), datetime.combine(day, closes, tz))
        for opens, closes in settings.opening_hours.get(local_now.weekday(), ())
    ]


def is_open(settings: RestaurantSettings, now: datetime) -> bool:
    local = _local(settings, now)
    return any(start <= local < end for start, end in _ranges_today(settings, local))


def available_slots(
    settings: RestaurantSettings,
    now: datetime,
    booked: Mapping[datetime, int] | None = None,
    limit: int = 12,
) -> list[datetime]:
    """Next free slots today, on the slot grid, at least ``min_lead_minutes``
    away, inside opening hours and below ``max_orders_per_slot``.
    Returned datetimes are in the restaurant's timezone."""
    booked = booked or {}
    local = _local(settings, now)
    earliest = local + timedelta(minutes=settings.min_lead_minutes)
    step = timedelta(minutes=settings.slot_minutes)

    slots: list[datetime] = []
    for start, end in _ranges_today(settings, local):
        slot = start
        while slot < earliest:
            slot += step
        # A slot is when the order is ready, so the closing time itself counts.
        while slot <= end and len(slots) < limit:
            if booked.get(slot, 0) < settings.max_orders_per_slot:
                slots.append(slot)
            slot += step
    return slots


def can_order(settings: RestaurantSettings, now: datetime,
              booked: Mapping[datetime, int] | None = None) -> bool:
    """Every order needs a slot with capacity ("prima possibile" takes the
    nearest one), so ordering is possible only while one is left today."""
    return bool(available_slots(settings, now, booked, limit=1))


def hours_text(settings: RestaurantSettings) -> str:
    """Opening hours as Italian text, one line per day."""
    days = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    lines = []
    for wd, name in enumerate(days):
        ranges = settings.opening_hours.get(wd, ())
        text = ", ".join(f"{o:%H:%M}–{c:%H:%M}" for o, c in ranges) or "chiuso"
        lines.append(f"{name}: {text}")
    return "\n".join(lines)

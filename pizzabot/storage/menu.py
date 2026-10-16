"""Restaurant settings + menu: DB rows ⇄ pizzabot.flow.menu objects."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg

from pizzabot.flow.machine import FlowContext
from pizzabot.flow.menu import (
    Category,
    Menu,
    MenuItem,
    Option,
    OptionGroup,
    RestaurantSettings,
)
from pizzabot.storage.errors import ConfigError
from pizzabot.storage.orders import booked_slots


def load_settings(conn: psycopg.Connection, restaurant_id: str) -> RestaurantSettings:
    row = conn.execute(
        """
        SELECT r.name, r.timezone, r.vat_rate, r.pickup_enabled, r.delivery_enabled,
               r.delivery_fee_eur, r.min_order_eur, r.delivery_postcodes,
               s.restaurant_id IS NOT NULL, s.slot_minutes, s.max_orders_per_slot,
               s.min_lead_minutes
          FROM restaurants r
          LEFT JOIN slot_settings s ON s.restaurant_id = r.id
         WHERE r.id = %s
        """,
        (restaurant_id,),
    ).fetchone()
    if row is None:
        raise ConfigError(f"unknown restaurant {restaurant_id!r}")
    (name, tz, vat_rate, pickup, delivery, fee, min_order, postcodes,
     has_slots, slot_minutes, max_per_slot, lead) = row
    # Never fall back to the Python default rate: IVA must be configured.
    if vat_rate is None:
        raise ConfigError(f"restaurant {restaurant_id!r} has no vat_rate configured")
    if not has_slots:
        raise ConfigError(f"restaurant {restaurant_id!r} has no slot_settings row")

    hours: dict[int, list] = defaultdict(list)
    for weekday, opens, closes in conn.execute(
        "SELECT weekday, opens_at, closes_at FROM opening_hours"
        " WHERE restaurant_id = %s ORDER BY weekday, opens_at",
        (restaurant_id,),
    ):
        hours[weekday].append((opens, closes))

    return RestaurantSettings(
        restaurant_id=restaurant_id,
        name=name,
        timezone=tz,
        vat_rate=vat_rate,
        pickup_enabled=pickup,
        delivery_enabled=delivery,
        delivery_fee_eur=fee,
        min_order_eur=min_order,
        delivery_postcodes=frozenset(postcodes),
        opening_hours={wd: tuple(r) for wd, r in hours.items()},
        slot_minutes=slot_minutes,
        max_orders_per_slot=max_per_slot,
        min_lead_minutes=lead,
    )


def load_menu(conn: psycopg.Connection, restaurant_id: str) -> Menu:
    """Active categories and their items, in display order. Items of an
    inactive category are left out, so a cart line pointing at one is
    treated as unavailable by the flow."""
    categories = tuple(
        Category(id_, name)
        for id_, name in conn.execute(
            "SELECT id, name FROM categories WHERE restaurant_id = %s AND active"
            " ORDER BY position, id",
            (restaurant_id,),
        )
    )

    groups_of: dict[str, list[str]] = defaultdict(list)
    for item_id, group_id in conn.execute(
        "SELECT menu_item_id, option_group_id FROM item_option_groups"
        " WHERE restaurant_id = %s ORDER BY menu_item_id, position, option_group_id",
        (restaurant_id,),
    ):
        groups_of[item_id].append(group_id)

    items = tuple(
        MenuItem(
            id=id_, category_id=cat_id, name=name, base_price_eur=price,
            description=desc, allergens=frozenset(allergens),
            option_group_ids=tuple(groups_of.get(id_, ())),
            available=available, age_restricted=age_restricted,
        )
        for id_, cat_id, name, price, desc, allergens, available, age_restricted in conn.execute(
            """
            SELECT mi.id, mi.category_id, mi.name, mi.base_price_eur, mi.description,
                   mi.allergens, mi.available, mi.age_restricted
              FROM menu_items mi
              JOIN categories c ON c.restaurant_id = mi.restaurant_id AND c.id = mi.category_id
             WHERE mi.restaurant_id = %s AND c.active
             ORDER BY mi.position, mi.id
            """,
            (restaurant_id,),
        )
    )

    options_of: dict[str, list[Option]] = defaultdict(list)
    for group_id, id_, name, delta, allergens, available in conn.execute(
        "SELECT option_group_id, id, name, price_delta_eur, allergens, available FROM options"
        " WHERE restaurant_id = %s ORDER BY option_group_id, position, id",
        (restaurant_id,),
    ):
        options_of[group_id].append(Option(id_, name, delta, frozenset(allergens), available))

    groups = tuple(
        OptionGroup(id_, name, tuple(options_of.get(id_, ())), min_select, max_select)
        for id_, name, min_select, max_select in conn.execute(
            "SELECT id, name, min_select, max_select FROM option_groups"
            " WHERE restaurant_id = %s ORDER BY position, id",
            (restaurant_id,),
        )
    )
    return Menu(categories=categories, items=items, option_groups=groups)


def load_flow_context(conn: psycopg.Connection, restaurant_id: str, now: datetime) -> FlowContext:
    """Everything the state machine needs for one event of one restaurant.
    booked_slots covers the restaurant's local day of ``now`` (orders are
    same-day only, [A9])."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    settings = load_settings(conn, restaurant_id)
    menu = load_menu(conn, restaurant_id)
    day = now.astimezone(ZoneInfo(settings.timezone)).date()
    return FlowContext(settings, menu, now, booked_slots(conn, restaurant_id, day))


def save_restaurant(conn: psycopg.Connection, settings: RestaurantSettings, menu: Menu) -> None:
    """Upsert a restaurant, its settings and its menu. Display order is the
    order of the Menu tuples. Opening hours and item ↔ option-group links are
    replaced; menu rows missing from ``menu`` are left as they are (no deletes,
    old orders don't reference them anyway). Runs in a savepoint."""
    rid = settings.restaurant_id
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO restaurants (id, name, timezone, vat_rate, pickup_enabled,
                                     delivery_enabled, delivery_fee_eur, min_order_eur,
                                     delivery_postcodes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, timezone = EXCLUDED.timezone,
                vat_rate = EXCLUDED.vat_rate, pickup_enabled = EXCLUDED.pickup_enabled,
                delivery_enabled = EXCLUDED.delivery_enabled,
                delivery_fee_eur = EXCLUDED.delivery_fee_eur,
                min_order_eur = EXCLUDED.min_order_eur,
                delivery_postcodes = EXCLUDED.delivery_postcodes
            """,
            (rid, settings.name, settings.timezone, settings.vat_rate, settings.pickup_enabled,
             settings.delivery_enabled, settings.delivery_fee_eur, settings.min_order_eur,
             sorted(settings.delivery_postcodes)),
        )
        conn.execute(
            """
            INSERT INTO slot_settings (restaurant_id, slot_minutes, max_orders_per_slot,
                                       min_lead_minutes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (restaurant_id) DO UPDATE SET
                slot_minutes = EXCLUDED.slot_minutes,
                max_orders_per_slot = EXCLUDED.max_orders_per_slot,
                min_lead_minutes = EXCLUDED.min_lead_minutes
            """,
            (rid, settings.slot_minutes, settings.max_orders_per_slot, settings.min_lead_minutes),
        )
        conn.execute("DELETE FROM opening_hours WHERE restaurant_id = %s", (rid,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO opening_hours (restaurant_id, weekday, opens_at, closes_at)"
                " VALUES (%s, %s, %s, %s)",
                [(rid, wd, o, c) for wd, ranges in sorted(settings.opening_hours.items())
                 for o, c in ranges],
            )
            cur.executemany(
                """
                INSERT INTO categories (restaurant_id, id, name, position, active)
                VALUES (%s, %s, %s, %s, true)
                ON CONFLICT (restaurant_id, id) DO UPDATE SET
                    name = EXCLUDED.name, position = EXCLUDED.position, active = true
                """,
                [(rid, c.id, c.name, pos) for pos, c in enumerate(menu.categories)],
            )
            cur.executemany(
                """
                INSERT INTO option_groups (restaurant_id, id, name, position, min_select, max_select)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (restaurant_id, id) DO UPDATE SET
                    name = EXCLUDED.name, position = EXCLUDED.position,
                    min_select = EXCLUDED.min_select, max_select = EXCLUDED.max_select
                """,
                [(rid, g.id, g.name, pos, g.min_select, g.max_select)
                 for pos, g in enumerate(menu.option_groups)],
            )
            cur.executemany(
                """
                INSERT INTO options (restaurant_id, id, option_group_id, name, price_delta_eur,
                                     allergens, available, position)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (restaurant_id, id) DO UPDATE SET
                    option_group_id = EXCLUDED.option_group_id, name = EXCLUDED.name,
                    price_delta_eur = EXCLUDED.price_delta_eur, allergens = EXCLUDED.allergens,
                    available = EXCLUDED.available, position = EXCLUDED.position
                """,
                [(rid, o.id, g.id, o.name, o.price_delta_eur, sorted(o.allergens), o.available, pos)
                 for g in menu.option_groups for pos, o in enumerate(g.options)],
            )
            cur.executemany(
                """
                INSERT INTO menu_items (restaurant_id, id, category_id, name, description,
                                        base_price_eur, allergens, available, position,
                                        age_restricted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (restaurant_id, id) DO UPDATE SET
                    category_id = EXCLUDED.category_id, name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    base_price_eur = EXCLUDED.base_price_eur, allergens = EXCLUDED.allergens,
                    available = EXCLUDED.available, position = EXCLUDED.position,
                    age_restricted = EXCLUDED.age_restricted
                """,
                [(rid, i.id, i.category_id, i.name, i.description, i.base_price_eur,
                  sorted(i.allergens), i.available, pos, i.age_restricted)
                 for pos, i in enumerate(menu.items)],
            )
            conn.execute("DELETE FROM item_option_groups WHERE restaurant_id = %s", (rid,))
            cur.executemany(
                "INSERT INTO item_option_groups (restaurant_id, menu_item_id, option_group_id,"
                " position) VALUES (%s, %s, %s, %s)",
                [(rid, i.id, gid, pos) for i in menu.items
                 for pos, gid in enumerate(i.option_group_ids)],
            )

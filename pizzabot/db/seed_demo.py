# seed_demo.py — `python -m pizzabot.db.seed_demo`
# Inserts (or refreshes) the demo restaurant from pizzabot/flow/demo.py.
# Safe to re-run: everything is an upsert. Prints the restaurant_id.

import dataclasses
import os
import sys
from decimal import Decimal

import psycopg

from pizzabot.flow.demo import demo_menu, demo_settings
from pizzabot.flow.menu import RestaurantSettings
from pizzabot.storage import save_restaurant

# Set explicitly, never taken from the Python default. 10 % is the usual
# Italian rate for takeaway / delivered food; AWAITS ACCOUNTANT CONFIRMATION
# (CONTEXT.md §3, Fiscal).
DEMO_VAT_RATE = Decimal("0.10")


def demo_restaurant_settings() -> RestaurantSettings:
    return dataclasses.replace(demo_settings(), vat_rate=DEMO_VAT_RATE)


def seed_demo(conn: psycopg.Connection) -> str:
    settings = demo_restaurant_settings()
    save_restaurant(conn, settings, demo_menu())
    return settings.restaurant_id


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("seed error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            with conn.transaction():
                restaurant_id = seed_demo(conn)
    except psycopg.Error as exc:
        detail = exc.diag.message_primary or type(exc).__name__
        print(f"seed error: {detail}", file=sys.stderr)
        return 1
    print(restaurant_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())

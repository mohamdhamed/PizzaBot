# set_kitchen_chat.py — `python -m pizzabot.db.set_kitchen_chat <restaurant_id> <chat_id>`
# Sets restaurants.kitchen_telegram_chat_id: the Telegram group that receives
# new orders. Group ids are negative (supergroups start with -100). Pass
# "none" to clear it (orders then stay pending until a chat is set).

import os
import sys

import psycopg

from pizzabot.storage import ConfigError, set_kitchen_chat_id

USAGE = "usage: python -m pizzabot.db.set_kitchen_chat <restaurant_id> <chat_id|none>"


def parse_chat_id(text: str) -> int | None:
    if text.lower() == "none":
        return None
    chat_id = int(text)  # ValueError on junk
    if chat_id == 0:
        raise ValueError("chat id 0")
    return chat_id


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    restaurant_id, raw = argv
    try:
        chat_id = parse_chat_id(raw)
    except ValueError:
        print(f"error: {raw!r} is not a Telegram chat id (an integer, e.g. -1001234567890)",
              file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("error: DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            with conn.transaction():
                set_kitchen_chat_id(conn, restaurant_id, chat_id)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"error: {exc.diag.message_primary or type(exc).__name__}", file=sys.stderr)
        return 1
    if chat_id is None:
        print(f"kitchen chat cleared for {restaurant_id}")
    else:
        print(f"kitchen chat for {restaurant_id} set to {chat_id}")
        if chat_id > 0:
            print("note: positive ids are private chats; a kitchen group id is negative",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""`python -m pizzabot.channels.telegram.find_chats` — list the group chats
the bot has recently seen, to find the kitchen group's chat_id.

Stop the bot first (`docker compose stop app`): only one getUpdates client
may run at a time, and the running bot would consume the update. Then write
any message in the kitchen group (e.g. /start@YourBot) and run this.
Updates are only read, not confirmed: the bot still gets them afterwards.
"""

from __future__ import annotations

import os
import sys

from pizzabot.channels.telegram.api import TelegramAPI, TelegramError


def group_chats(updates: list[dict]) -> dict[int, tuple[str, str]]:
    found: dict[int, tuple[str, str]] = {}
    for update in updates:
        for key in ("message", "edited_message", "my_chat_member", "channel_post"):
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("type") in ("group", "supergroup") and isinstance(chat.get("id"), int):
                found[chat["id"]] = (chat.get("type"), chat.get("title") or "")
    return found


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("error: TELEGRAM_BOT_TOKEN is not set", file=sys.stderr)
        return 2
    try:
        updates = TelegramAPI(token).call(
            "getUpdates", {"timeout": 0, "allowed_updates": ["message", "my_chat_member"]})
    except TelegramError as exc:
        print(f"error: {exc.description}", file=sys.stderr)
        if exc.error_code == 409:
            print("stop the bot first: docker compose stop app", file=sys.stderr)
        return 1
    chats = group_chats(updates or [])
    if not chats:
        print("no group seen yet: add the bot to the group, send a message there "
              "(e.g. /start@YourBot), then run this again")
        return 1
    for chat_id, (kind, title) in chats.items():
        print(f"{chat_id}\t{kind}\t{title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

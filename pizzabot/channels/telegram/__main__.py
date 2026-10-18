"""`python -m pizzabot.channels.telegram` — migrate, check config, then poll.

Environment (from .env via docker compose):
    DATABASE_URL         PostgreSQL connection string
    TELEGRAM_BOT_TOKEN   from @BotFather — never logged
    RESTAURANT_ID        the one restaurant this bot serves (must exist in the DB)

Exits 2 on a configuration problem (missing variable, unknown restaurant,
token rejected), so a misconfigured container fails fast and visibly.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

import psycopg

from pizzabot.channels.telegram.api import TelegramAPI, TelegramError
from pizzabot.channels.telegram.bot import TelegramBot, db_connect, utc_now
from pizzabot.channels.telegram.kitchen import KitchenNotifier, describe
from pizzabot.db.migrate import MigrationError, migrate
from pizzabot.storage import ConfigError, kitchen_chat_id, load_settings

log = logging.getLogger("pizzabot.telegram")


class RedactFilter(logging.Filter):
    """Last line of defence: replace secrets in any formatted log record."""

    def __init__(self, *secrets: str):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = None
        for secret in self.secrets:
            msg = msg.replace(secret, "<redacted>")
        record.msg, record.args = msg, None
        return True


def setup_logging(*secrets: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter(*secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    restaurant_id = os.environ.get("RESTAURANT_ID", "").strip()
    url = os.environ.get("DATABASE_URL", "").strip()
    setup_logging(token)

    missing = [name for name, value in (("DATABASE_URL", url), ("TELEGRAM_BOT_TOKEN", token),
                                        ("RESTAURANT_ID", restaurant_id)) if not value]
    if missing:
        log.error("missing configuration: %s (set it in .env)", ", ".join(missing))
        return 2

    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            for version in migrate(conn):
                log.info("migration applied: %s", version)
        with db_connect(url) as conn:
            with conn.transaction():
                settings = load_settings(conn, restaurant_id)
                kitchen = kitchen_chat_id(conn, restaurant_id)
    except ConfigError as exc:
        log.error("restaurant %r is not usable: %s", restaurant_id, exc)
        return 2
    except MigrationError as exc:
        log.error("migration error: %s", exc)
        return 1
    except psycopg.Error as exc:
        log.error("database error at startup: %s", describe(exc))
        return 1

    api = TelegramAPI(token)
    try:
        me = api.call("getMe")
    except TelegramError as exc:
        log.error("Telegram rejected the bot token or is unreachable: %s", exc.description)
        return 2 if exc.error_code in (401, 404) else 1
    log.info("bot @%s serving restaurant=%s (%s)", me.get("username"), restaurant_id,
             settings.timezone)
    if kitchen is None:
        log.warning("restaurant=%s has no kitchen chat: orders will stay pending. Run: "
                    "python -m pizzabot.db.set_kitchen_chat %s <chat_id>", restaurant_id,
                    restaurant_id)

    # The kitchen gets its own client with fewer in-line retries: a failed
    # notification is retried from the loop, it must not stall customers.
    notifier = KitchenNotifier(TelegramAPI(token, max_retries=1), restaurant_id, clock=utc_now)
    bot = TelegramBot(api, lambda: db_connect(url), restaurant_id, notifier=notifier)

    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()
        if bot.polling:  # blocked in getUpdates: leave now, nothing half-done
            raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        bot.run(stop.is_set)
    except TelegramError as exc:
        log.error("stopping: %s", exc.description)
        return 2
    finally:
        bot.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

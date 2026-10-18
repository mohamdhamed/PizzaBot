"""PostgreSQL storage for the guided flow — plain psycopg, no ORM.

Every function takes an open connection and never commits: the caller owns
the transaction (see ``pizzabot.service.process_event``). Nothing here reads
the clock; ``now`` is always passed in.
"""

from pizzabot.storage.conversations import (
    IDLE_TIMEOUT,
    load_conversation,
    save_conversation,
)
from pizzabot.storage.errors import ConfigError, StorageError
from pizzabot.storage.menu import load_flow_context, load_menu, load_settings, save_restaurant
from pizzabot.storage.orders import (
    StoredLine,
    StoredOption,
    StoredOrder,
    booked_slots,
    count_stale_kitchen_orders,
    kitchen_chat_id,
    load_order,
    mark_kitchen_notified,
    pending_kitchen_orders,
    save_order,
    set_kitchen_chat_id,
)

__all__ = [
    "IDLE_TIMEOUT", "ConfigError", "StorageError", "StoredLine", "StoredOption", "StoredOrder",
    "booked_slots", "count_stale_kitchen_orders", "kitchen_chat_id", "load_conversation",
    "load_flow_context", "load_menu", "load_order", "load_settings", "mark_kitchen_notified",
    "pending_kitchen_orders", "save_conversation", "save_order", "save_restaurant",
    "set_kitchen_chat_id",
]

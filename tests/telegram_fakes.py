# telegram_fakes.py — a fake Bot API for the adapter tests (no network).

from pizzabot.channels.telegram.api import TelegramError


class FakeAPI:
    """Records every call. ``fail[method]`` is a list of exceptions raised by
    the next calls of that method (None = succeed)."""

    def __init__(self, updates=None):
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, list] = {}
        self.updates: list[list[dict]] = list(updates or [])
        self.next_message_id = 100

    def call(self, method, params=None, *, timeout=None):
        params = params or {}
        self.calls.append((method, params))
        queue = self.fail.get(method)
        if queue:
            exc = queue.pop(0)
            if exc is not None:
                raise exc
        if method == "sendMessage":
            self.next_message_id += 1
            return {"message_id": self.next_message_id, "chat": {"id": params["chat_id"]}}
        if method == "editMessageText":
            return {"message_id": params["message_id"]}
        if method == "getUpdates":
            return self.updates.pop(0) if self.updates else []
        if method == "getMe":
            return {"username": "pizzabot_test_bot"}
        return True

    def sent(self, method=None, chat_id=None):
        return [p for m, p in self.calls
                if (method is None or m == method)
                and (chat_id is None or p.get("chat_id") == chat_id)]

    def methods(self):
        return [m for m, _ in self.calls]


def not_modified():
    return TelegramError("editMessageText", "Bad Request: message is not modified",
                         error_code=400)


def network_down(method="sendMessage"):
    return TelegramError(method, "network error: timeout")


# --- update builders --------------------------------------------------------------

USER = {"id": 4242, "is_bot": False, "first_name": "Mario"}
PRIVATE = {"id": 4242, "type": "private", "first_name": "Mario"}
GROUP = {"id": -1001234567890, "type": "supergroup", "title": "Cucina"}


def text_update(uid, text, chat=PRIVATE, user=USER):
    return {"update_id": uid, "message": {"message_id": uid, "date": 1, "chat": chat,
                                          "from": user, "text": text}}


def message_update(uid, chat=PRIVATE, user=USER, **content):
    return {"update_id": uid, "message": {"message_id": uid, "date": 1, "chat": chat,
                                          "from": user, **content}}


def callback_update(uid, data, message_id, chat=PRIVATE, user=USER):
    return {"update_id": uid, "callback_query": {
        "id": f"cb{uid}", "from": user, "data": data, "chat_instance": "x",
        "message": {"message_id": message_id, "date": 1, "chat": chat}}}


def buttons(params) -> list[str]:
    """callback_data of every button in a sendMessage/editMessageText call."""
    markup = params.get("reply_markup") or {"inline_keyboard": []}
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]

"""Screen → Telegram message: text + inline keyboard, with pagination.

Pagination is adapter-only. Page buttons carry ``~pg:<n>`` callback data;
machine option ids never start with ``~`` (enforced below), so the two can
never collide. Turning a page does not touch the state machine: the bot
keeps the last screen it sent to each chat and re-renders that screen at
another page (see ``bot.ScreenCache``). Every real button still goes
through ``handle``, which validates it against the *current* screen.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from pizzabot.flow.machine import Screen, ScreenOption

NAV_PREFIX = "~"
PAGE_PREFIX = "~pg:"

PAGE_SIZE = 8                 # paginated options per page
MAX_TEXT = 4096               # Telegram message length (after entity parsing)
MAX_CALLBACK_BYTES = 64       # Telegram callback_data limit
MAX_LABEL = 40                # longer labels get cut on phones anyway

# Shown on every page, after the page's own options.
FIXED_IDS = ("next", "cart", "menu")
NAV_IDS = ("back", "cancel")  # always together on the last row

_BOLD_RE = re.compile(r"\*([^*\n]+)\*")
_PAGE_RE = re.compile(r"^~pg:(\d{1,3})$")


@dataclass(frozen=True)
class Rendered:
    text: str                       # HTML (parse_mode="HTML")
    reply_markup: dict | None
    page: int
    pages: int


def callback_ok(data: str) -> bool:
    return 0 < len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES


def parse_page(data: str) -> int | None:
    """The page number of a ``~pg:<n>`` button, else None."""
    m = _PAGE_RE.match(data)
    return int(m.group(1)) if m else None


def _split(screen: Screen) -> tuple[list[ScreenOption], list[ScreenOption], list[ScreenOption]]:
    body, fixed, nav = [], [], []
    for o in screen.options:
        if o.id.startswith(NAV_PREFIX):
            raise ValueError(f"option id {o.id!r} uses the adapter's reserved prefix")
        if not callback_ok(o.id):
            raise ValueError(f"option id {o.id!r} does not fit Telegram callback_data")
        (nav if o.id in NAV_IDS else fixed if o.id in FIXED_IDS else body).append(o)
    return body, fixed, nav


def page_count(screen: Screen) -> int:
    body = _split(screen)[0]
    return max(1, -(-len(body) // PAGE_SIZE))


def label(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


def _per_row(text: str) -> int:
    n = len(text)
    return 4 if n <= 6 else 3 if n <= 10 else 2 if n <= 16 else 1


def _rows(options: list[ScreenOption]) -> list[list[dict]]:
    """One button per row, except short labels, which share a row."""
    rows: list[list[dict]] = []
    row: list[dict] = []
    cap = 0
    for o in options:
        text = label(o.label)
        n = _per_row(text)
        if row and len(row) < min(cap, n):
            row.append({"text": text, "callback_data": o.id})
            cap = min(cap, n)
            continue
        if row:
            rows.append(row)
        row, cap = [{"text": text, "callback_data": o.id}], n
    if row:
        rows.append(row)
    return rows


def to_html(text: str) -> str:
    """Escape everything (customer text included), then turn the machine's
    ``*bold*`` into <b>."""
    return _BOLD_RE.sub(r"<b>\1</b>", html.escape(text, quote=False))


def fit_text(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_screen(screen: Screen, page: int = 0) -> Rendered:
    body, fixed, nav = _split(screen)
    pages = max(1, -(-len(body) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)

    keyboard = _rows(body[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])
    if pages > 1:
        pager = []
        if page > 0:
            pager.append({"text": "◀ Precedenti", "callback_data": f"{PAGE_PREFIX}{page - 1}"})
        if page < pages - 1:
            pager.append({"text": "Altri ▶", "callback_data": f"{PAGE_PREFIX}{page + 1}"})
        keyboard.append(pager)
    keyboard += [[{"text": label(o.label), "callback_data": o.id}] for o in fixed]
    if nav:
        keyboard.append([{"text": label(o.label), "callback_data": o.id} for o in nav])

    text = screen.text
    suffix = f"\n\n(pagina {page + 1}/{pages})" if pages > 1 else ""
    text = fit_text(text, MAX_TEXT - len(suffix)) + suffix
    markup = {"inline_keyboard": keyboard} if keyboard else None
    return Rendered(to_html(text), markup, page, pages)

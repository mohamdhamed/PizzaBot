# test_telegram_render.py — Screen → Telegram message + keyboard, pagination.

import pytest

from pizzabot.channels.telegram.render import (
    MAX_LABEL, MAX_TEXT, PAGE_SIZE, callback_ok, page_count, parse_page, render_screen,
)
from pizzabot.flow import Choice, Conversation, FlowContext, Screen, ScreenOption, Text, handle, render
from pizzabot.flow.demo import demo_menu, demo_settings
from pizzabot.flow.machine import BACK, CANCEL
from tests.test_flow_machine import at


def screen_with(n, *, extra=(), nav=True):
    opts = tuple(ScreenOption(f"item:i{k}", f"Pizza numero {k} — 6,00 €") for k in range(n))
    return Screen("Pizze", opts + tuple(extra) + ((BACK, CANCEL) if nav else ()))


def rows(r):
    return [[b["callback_data"] for b in row] for row in r.reply_markup["inline_keyboard"]]


def flat(r):
    return [cid for row in rows(r) for cid in row]


# --- layout -------------------------------------------------------------------------

def test_long_labels_one_per_row_and_nav_together_last():
    r = render_screen(screen_with(3))
    assert rows(r) == [["item:i0"], ["item:i1"], ["item:i2"], ["back", "cancel"]]


def test_short_labels_share_rows():
    qty = Screen("Quante?", tuple(ScreenOption(f"qty:{n}", str(n)) for n in range(1, 10)))
    assert [len(row) for row in rows(render_screen(qty))] == [4, 4, 1]
    detail = Screen("Margherita", (ScreenOption("add", "➕ Aggiungi"), ScreenOption("add:2", "×2"),
                                   ScreenOption("add:3", "×3"), ScreenOption("custom", "⚙️ Personalizza")))
    assert rows(render_screen(detail)) == [["add", "add:2", "add:3"], ["custom"]]


def test_text_is_escaped_and_machine_bold_becomes_html():
    r = render_screen(Screen("*Margherita* — 6,00 €\n📝 <b>senza</b> & cipolla"))
    assert r.text == "<b>Margherita</b> — 6,00 €\n📝 &lt;b&gt;senza&lt;/b&gt; &amp; cipolla"
    assert r.reply_markup is None


def test_long_text_and_labels_are_cut_to_telegram_limits():
    r = render_screen(Screen("x" * 5000, (ScreenOption("a", "y" * 100),)))
    assert len(r.text) == MAX_TEXT and r.text.endswith("…")
    (button,) = r.reply_markup["inline_keyboard"][0]
    assert len(button["text"]) == MAX_LABEL and button["text"].endswith("…")


# --- pagination ---------------------------------------------------------------------

def test_exactly_one_page_has_no_pager():
    r = render_screen(screen_with(PAGE_SIZE))
    assert r.pages == 1 and not any(c.startswith("~") for c in flat(r))
    assert "pagina" not in r.text


@pytest.mark.parametrize("n, pages", [(1, 1), (8, 1), (9, 2), (16, 2), (17, 3)])
def test_page_count_boundaries(n, pages):
    assert page_count(screen_with(n)) == pages


def test_one_over_the_page_size_makes_two_pages():
    s = screen_with(PAGE_SIZE + 1)
    first, second = render_screen(s, 0), render_screen(s, 1)
    assert flat(first) == [f"item:i{k}" for k in range(8)] + ["~pg:1", "back", "cancel"]
    assert flat(second) == ["item:i8", "~pg:0", "back", "cancel"]
    assert first.text.endswith("(pagina 1/2)") and second.text.endswith("(pagina 2/2)")


def test_middle_page_has_both_directions_and_fixed_buttons_on_every_page():
    cart = ScreenOption("cart", "🛒 Carrello (2) — 12,00 €")
    menu = ScreenOption("menu", "📂 Altre categorie")
    s = screen_with(17, extra=(cart, menu))
    for page in range(3):
        ids = flat(render_screen(s, page))
        assert ids[-4:] == ["cart", "menu", "back", "cancel"]
    middle = rows(render_screen(s, 1))
    assert ["~pg:0", "~pg:2"] in middle


def test_out_of_range_page_is_clamped():
    s = screen_with(9)
    assert render_screen(s, 99).page == 1 and render_screen(s, -3).page == 0


def test_parse_page():
    assert parse_page("~pg:0") == 0 and parse_page("~pg:12") == 12
    for bad in ("~pg:", "~pg:x", "pg:1", "~pg:1234", "~pg:-1", "cat:pizze"):
        assert parse_page(bad) is None


# --- callback_data ----------------------------------------------------------------------

def test_callback_data_limit_is_64_bytes():
    assert callback_ok("a" * 64) and not callback_ok("a" * 65)
    assert callback_ok("é" * 32) and not callback_ok("é" * 33)  # 2 bytes each
    assert not callback_ok("")


def test_screen_with_oversized_or_reserved_id_is_refused():
    with pytest.raises(ValueError, match="callback_data"):
        render_screen(Screen("x", (ScreenOption("item:" + "x" * 60, "Troppo lungo"),)))
    with pytest.raises(ValueError, match="reserved"):
        render_screen(Screen("x", (ScreenOption("~pg:1", "Finto"),)))


def test_every_demo_screen_renders_within_limits():
    """Walk the demo flow and render every screen on the way."""
    settings, menu = demo_settings(), demo_menu()
    ctx = FlowContext(settings, menu, at(19))
    conv = Conversation(restaurant_id="demo", channel="telegram", customer_ref="1")
    path = ["order", "cat:classiche", "item:margherita", "custom", "opt:maxi", "opt:classico",
            "opt:bufala", "next", "qty:2", "cart", "proceed", "delivery"]
    screens = [render(conv, ctx)]
    for cid in path:
        result = handle(conv, Choice(cid), ctx)
        conv = result.conversation
        screens.append(result.screen)
    result = handle(conv, Text("Via Roma 1, 20900 Monza"), ctx)
    screens.append(result.screen)
    for screen in screens:
        for page in range(page_count(screen)):
            r = render_screen(screen, page)
            for row in (r.reply_markup or {"inline_keyboard": []})["inline_keyboard"]:
                for b in row:
                    assert callback_ok(b["callback_data"]) and len(b["text"]) <= MAX_LABEL

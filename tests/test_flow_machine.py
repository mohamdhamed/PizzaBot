# tests/test_flow_machine.py — الـ guided flow من أول رسالة لحد تأكيد الطلب

from dataclasses import replace
from datetime import datetime, time
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from pizzabot.flow import (
    Category, Choice, Conversation, FlowContext, Menu, MenuItem, Option,
    OptionGroup, PlaceOrder, RestaurantSettings, State, Text, handle, render,
)
from pizzabot.flow.machine import MSG_CANCELLED, MSG_STALE, MSG_USE_BUTTONS
from pizzabot.flow.menu import MenuError
from pizzabot.flow import slots
from pizzabot.order import Fulfilment

ROME = ZoneInfo("Europe/Rome")


def at(hh: int, mm: int = 0) -> datetime:
    # Thursday 2026-10-15, restaurant local time.
    return datetime(2026, 10, 15, hh, mm, tzinfo=ROME)


# --- reference menu (docs/design-v1.md §1, trimmed) ---------------------------

DIM = OptionGroup("dim", "Dimensione", (
    Option("normale", "Normale"),
    Option("maxi", "Maxi", D("4.00")),
    Option("baby", "Baby", D("-1.00")),
))
IMPASTO = OptionGroup("impasto", "Impasto", (
    Option("classico", "Classico"),
    Option("integrale", "Integrale", D("1.00")),
    Option("sg", "Senza glutine", D("2.50")),
))
AGGIUNTE = OptionGroup("agg", "Aggiunte", (
    Option("bufala", "Mozzarella di bufala", D("2.00"), allergens=frozenset({7})),
    Option("funghi", "Funghi", D("1.00")),
    Option("olive", "Olive", D("0.50")),
    Option("crudo", "Prosciutto crudo", D("2.00")),
), min_select=0, max_select=3)

PIZZA_GROUPS = ("dim", "impasto", "agg")


def make_menu(**overrides) -> Menu:
    items = {
        "marg": MenuItem("marg", "pizze", "Margherita", D("6.00"),
                         description="pomodoro, mozzarella, basilico",
                         allergens=frozenset({1, 7}), option_group_ids=PIZZA_GROUPS),
        "diav": MenuItem("diav", "pizze", "Diavola", D("7.50"),
                         allergens=frozenset({1, 7}), option_group_ids=PIZZA_GROUPS),
        "acqua": MenuItem("acqua", "bevande", "Acqua 50cl", D("1.50")),
        "birra": MenuItem("birra", "bevande", "Birra Moretti 33cl", D("3.50"),
                          allergens=frozenset({1}), age_restricted=True),
    }
    items.update(overrides)
    return Menu(
        categories=(Category("pizze", "Pizze"), Category("bevande", "Bevande")),
        items=tuple(items.values()),
        option_groups=(DIM, IMPASTO, AGGIUNTE),
    )


EVENING = ((time(12, 0), time(14, 30)), (time(18, 30), time(22, 30)))

SETTINGS = RestaurantSettings(
    restaurant_id="rest-monza-01",
    name="Pizzeria Da Test",
    delivery_fee_eur=D("2.50"),
    min_order_eur=D("10.00"),
    delivery_postcodes=frozenset({"20900"}),
    opening_hours={wd: EVENING for wd in range(7)},
)


class Bot:
    """Drives one conversation like a channel adapter would."""

    def __init__(self, *, menu=None, settings=SETTINGS, now=None, booked=None):
        self.ctx = FlowContext(settings, menu or make_menu(), now or at(19), booked or {})
        self.conv = Conversation(restaurant_id=settings.restaurant_id,
                                 channel="telegram", customer_ref="42")
        self.screen = render(self.conv, self.ctx)
        self.effects = ()

    def press(self, cid: str):
        assert cid in self.ids, f"{cid!r} not on screen {sorted(self.ids)}"
        return self._send(Choice(cid))

    def press_stale(self, cid: str):
        return self._send(Choice(cid))

    def type(self, text: str):
        return self._send(Text(text))

    def _send(self, event):
        result = handle(self.conv, event, self.ctx)
        self.conv, self.screen, self.effects = result.conversation, result.screen, result.effects
        return self.screen

    def with_ctx(self, **changes):
        self.ctx = replace(self.ctx, **changes)

    @property
    def ids(self) -> set[str]:
        return self.screen.option_ids()


def margherita_maxi_bufala(bot: Bot, qty: int = 2):
    bot.press("order")
    bot.press("cat:pizze")
    bot.press("item:marg")
    bot.press("custom")
    bot.press("opt:maxi")
    bot.press("opt:classico")
    bot.press("opt:bufala")
    bot.press("next")
    bot.press(f"qty:{qty}")


# --- start / closed ------------------------------------------------------------

def test_start_screen_and_any_text_keeps_it():
    bot = Bot()
    assert "Pizzeria Da Test" in bot.screen.text
    bot.type("/start")
    assert bot.conv.state is State.start
    assert bot.ids == {"order"}


def test_closed_after_last_slot():
    bot = Bot(now=at(23))
    bot.press("order")
    assert bot.conv.state is State.closed
    assert "Orari" in bot.screen.text


def test_can_order_ahead_while_closed_in_the_afternoon():
    bot = Bot(now=at(16))
    bot.press("order")
    assert bot.conv.state is State.category


# --- happy paths -------------------------------------------------------------------

def test_pickup_order_end_to_end():
    bot = Bot()
    margherita_maxi_bufala(bot)
    assert bot.conv.state is State.item
    assert bot.screen.text.startswith("✓ Aggiunto: 2× Margherita")
    bot.press("cart")
    assert "2× Margherita (Maxi, Classico, Mozzarella di bufala) — 24,00 €" in bot.screen.text
    bot.press("proceed")
    bot.press("pickup")
    bot.press("slot:asap")
    bot.press("skip")
    assert bot.conv.state is State.confirm
    assert "Totale: 24,00 €" in bot.screen.text
    bot.press("confirm")

    assert bot.conv.state is State.done
    assert bot.conv.cart == []
    (effect,) = bot.effects
    assert isinstance(effect, PlaceOrder)
    order = effect.order
    assert order.restaurant_id == "rest-monza-01"
    assert order.customer_ref == "42"
    assert order.fulfilment is Fulfilment.pickup
    assert order.slot_at is None
    assert order.kitchen_notes is None
    (line,) = order.items
    assert line.menu_item_id == "marg"
    assert line.unit_price_eur == D("12.00")  # 6 + 4 maxi + 2 bufala
    assert [o.name for o in line.options] == ["Maxi", "Classico", "Mozzarella di bufala"]
    assert order.total_eur == D("24.00")
    assert order.net_eur + order.vat_eur == order.total_eur


def test_delivery_order_with_slot_and_notes():
    bot = Bot()
    margherita_maxi_bufala(bot, qty=1)
    bot.press("cart")
    bot.press("proceed")
    bot.press("delivery")
    assert bot.screen.accepts_text
    bot.type("Via Roma 1, 20900 Monza")
    assert bot.conv.state is State.slot
    bot.press("slot:2000")
    bot.type("senza cipolla, ben cotta")
    assert "Consegna — 2,50 €" in bot.screen.text
    assert "senza cipolla, ben cotta" in bot.screen.text
    bot.press("confirm")

    order = bot.effects[0].order
    assert order.fulfilment is Fulfilment.delivery
    assert order.address == "Via Roma 1, 20900 Monza"
    assert order.slot_at == at(20)
    assert order.kitchen_notes == "senza cipolla, ben cotta"
    assert order.delivery_fee_eur == D("2.50")
    assert order.total_eur == D("14.50")


def test_drink_has_no_customize_button():
    bot = Bot()
    for cid in ["order", "cat:bevande", "item:acqua"]:
        bot.press(cid)
    assert "custom" not in bot.ids
    assert {"add", "add:2", "add:3"} <= bot.ids


# --- quick add (standard pizza) -----------------------------------------------------

def test_quick_add_uses_required_defaults_and_no_extras():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg"]:
        bot.press(cid)
    assert "Standard: Normale, Classico" in bot.screen.text
    bot.press("add")
    (line,) = bot.conv.cart
    assert line.option_ids == ["normale", "classico"]
    assert line.quantity == 1
    # Back on the same category, ready for the next pizza.
    assert bot.conv.state is State.item
    assert bot.conv.data["category_id"] == "pizze"
    assert "cart" in bot.ids


def test_quick_add_times_three():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:diav", "add:3"]:
        bot.press(cid)
    assert bot.conv.cart[0].quantity == 3


def test_three_different_pizzas_in_few_taps():
    bot = Bot()
    taps = ["order", "cat:pizze",
            "item:marg", "add",
            "item:diav", "add",
            "item:marg", "custom", "opt:maxi", "opt:classico", "next", "qty:1",
            "cart"]
    for cid in taps:
        bot.press(cid)
        assert not bot.screen.text.startswith(MSG_STALE), cid
    assert bot.conv.state is State.cart_review
    assert len(bot.conv.cart) == 3


def test_customize_preselects_defaults():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom"]:
        bot.press(cid)
    labels = {o.id: o.label for o in bot.screen.options}
    assert labels["opt:normale"].startswith("✓ ")
    assert not labels["opt:maxi"].startswith("✓ ")


def test_quick_add_skips_sold_out_default():
    menu = make_menu()
    dim = replace(menu.group("dim"), options=(
        Option("normale", "Normale", available=False),
        Option("maxi", "Maxi", D("4.00")),
    ))
    menu = replace(menu, option_groups=(dim, menu.group("impasto"), menu.group("agg")))
    bot = Bot(menu=menu)
    for cid in ["order", "cat:pizze", "item:marg", "add"]:
        bot.press(cid)
    assert bot.conv.cart[0].option_ids == ["maxi", "classico"]


# --- input rules ------------------------------------------------------------------------

def test_free_text_outside_text_steps_is_refused():
    bot = Bot()
    bot.press("order")
    bot.type("una margherita grande per favore")
    assert bot.conv.state is State.category
    assert bot.screen.text.startswith(MSG_USE_BUTTONS)


def test_stale_button_is_refused():
    bot = Bot()
    bot.press("order")
    bot.press_stale("item:marg")  # belongs to the item screen, not category
    assert bot.conv.state is State.category
    assert bot.screen.text.startswith(MSG_STALE)


def test_every_option_id_fits_telegram_callback_data():
    bot = Bot()
    margherita_maxi_bufala(bot)
    seen = set()
    for cid in ["cart", "proceed", "delivery"]:
        bot.press(cid)
        seen |= bot.ids
    bot.type("Via Roma 1, 20900 Monza")
    seen |= bot.ids
    assert all(len(i.encode()) <= 64 and i.isascii() for i in seen)


# --- options -------------------------------------------------------------------------------

def test_required_single_group_has_no_skip():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom"]:
        bot.press(cid)
    assert "next" not in bot.ids
    assert "opt:baby" in bot.ids
    assert any("−1,00 €" in o.label for o in bot.screen.options)


def test_multi_select_toggle_and_max():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom", "opt:normale", "opt:classico"]:
        bot.press(cid)
    for oid in ["bufala", "funghi", "olive"]:
        bot.press(f"opt:{oid}")
    bot.press("opt:crudo")
    assert "al massimo 3" in bot.screen.text
    bot.press("opt:olive")  # toggle off
    assert bot.conv.data["selected"]["agg"] == ["bufala", "funghi"]
    labels = {o.id: o.label for o in bot.screen.options}
    assert labels["opt:bufala"].startswith("✓ ")
    assert not labels["opt:olive"].startswith("✓ ")


def test_extras_can_be_skipped():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom", "opt:normale", "opt:classico", "next"]:
        bot.press(cid)
    assert bot.conv.state is State.quantity


def test_item_detail_shows_allergens_and_age_notice():
    bot = Bot()
    for cid in ["order", "cat:bevande", "item:birra"]:
        bot.press(cid)
    assert "Allergeni: Glutine" in bot.screen.text
    assert "minori di 18" in bot.screen.text


# --- navigation -----------------------------------------------------------------------------

def test_back_walks_option_groups_then_item():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom", "opt:maxi"]:
        bot.press(cid)
    assert bot.conv.data["group_index"] == 1
    bot.press("back")
    assert bot.conv.data["group_index"] == 0
    bot.press("back")
    assert bot.conv.state is State.item_detail
    bot.press("back")
    assert bot.conv.state is State.item
    bot.press("back")
    assert bot.conv.state is State.category


def test_back_from_quantity_returns_to_last_group():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom", "opt:maxi", "opt:classico", "next"]:
        bot.press(cid)
    bot.press("back")
    assert bot.conv.state is State.option_group
    assert bot.conv.data["group_index"] == 2


def test_cancel_empties_cart():
    bot = Bot()
    margherita_maxi_bufala(bot)
    bot.press("cancel")
    assert bot.conv.state is State.start
    assert bot.conv.cart == []
    assert bot.screen.text.startswith(MSG_CANCELLED)


# --- cart ------------------------------------------------------------------------------------

def test_identical_lines_merge():
    bot = Bot()
    margherita_maxi_bufala(bot, qty=1)
    for cid in ["item:marg", "custom", "opt:maxi", "opt:classico", "opt:bufala", "next", "qty:2"]:
        bot.press(cid)
    assert len(bot.conv.cart) == 1
    assert bot.conv.cart[0].quantity == 3


def test_edit_line_inc_dec_remove():
    bot = Bot()
    margherita_maxi_bufala(bot, qty=1)
    bot.press("cart")
    bot.press("edit")
    bot.press("line:0")
    bot.press("inc")
    assert bot.conv.cart[0].quantity == 2
    bot.press("edit"); bot.press("line:0"); bot.press("dec")
    assert bot.conv.cart[0].quantity == 1
    bot.press("edit"); bot.press("line:0"); bot.press("rm")
    assert bot.conv.cart == []
    assert "vuoto" in bot.screen.text


def test_minimum_order_blocks_proceed():
    bot = Bot()
    for cid in ["order", "cat:bevande", "item:acqua", "add", "cart"]:
        bot.press(cid)
    assert "proceed" not in bot.ids
    assert "Ordine minimo: 10,00 €" in bot.screen.text


def test_only_pickup_skips_fulfilment_question():
    bot = Bot(settings=replace(SETTINGS, delivery_enabled=False))
    margherita_maxi_bufala(bot)
    bot.press("cart")
    bot.press("proceed")
    assert bot.conv.state is State.slot


# --- address & notes ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,msg", [
    ("Via Roma 1, Monza", "CAP"),
    ("Via Roma 1, 20100 Milano", "non consegniamo"),
    ("abc", "troppo corto"),
])
def test_bad_address_stays_on_address(text, msg):
    bot = Bot()
    margherita_maxi_bufala(bot)
    for cid in ["cart", "proceed", "delivery"]:
        bot.press(cid)
    bot.type(text)
    assert bot.conv.state is State.address
    assert msg in bot.screen.text


def test_notes_too_long_and_control_chars():
    bot = Bot()
    margherita_maxi_bufala(bot)
    for cid in ["cart", "proceed", "pickup", "slot:asap"]:
        bot.press(cid)
    bot.type("x" * 301)
    assert bot.conv.state is State.notes
    assert "troppo lunga" in bot.screen.text
    bot.type("ben cotta\x00\x1b")
    assert bot.conv.data["notes"] == "ben cotta"


# --- the world changes mid-conversation -------------------------------------------------------

def test_item_sold_out_while_in_cart_is_dropped():
    bot = Bot()
    margherita_maxi_bufala(bot)
    bot.with_ctx(menu=make_menu(marg=replace(make_menu().item("marg"), available=False)))
    bot.press("cart")
    assert bot.conv.cart == []
    assert "Non più disponibile" in bot.screen.text


def test_sold_out_before_confirm_returns_to_cart_without_order():
    bot = Bot()
    margherita_maxi_bufala(bot)
    for cid in ["cart", "proceed", "pickup", "slot:asap", "skip"]:
        bot.press(cid)
    bot.with_ctx(menu=make_menu(marg=replace(make_menu().item("marg"), available=False)))
    bot.press("confirm")
    assert bot.effects == ()
    assert bot.conv.state is State.cart_review
    assert "Non più disponibile" in bot.screen.text


def test_price_change_before_confirm_uses_menu_price():
    bot = Bot()
    margherita_maxi_bufala(bot, qty=1)
    for cid in ["cart", "proceed", "pickup", "slot:asap", "skip"]:
        bot.press(cid)
    bot.with_ctx(menu=make_menu(marg=replace(make_menu().item("marg"), base_price_eur=D("7.00"))))
    bot.press("confirm")
    assert bot.effects[0].order.total_eur == D("13.00")


def test_slot_filled_before_confirm_asks_again():
    bot = Bot()
    margherita_maxi_bufala(bot)
    for cid in ["cart", "proceed", "pickup", "slot:2000", "skip"]:
        bot.press(cid)
    bot.with_ctx(booked_slots={at(20): SETTINGS.max_orders_per_slot})
    bot.press("confirm")
    assert bot.effects == ()
    assert bot.conv.state is State.slot
    assert "slot:2000" not in bot.ids


def test_item_removed_mid_options_goes_back_to_categories():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:diav", "custom"]:
        bot.press(cid)
    bot.with_ctx(menu=make_menu(diav=replace(make_menu().item("diav"), available=False)))
    bot.press("opt:maxi")
    assert bot.conv.state is State.category
    assert "non è più disponibile" in bot.screen.text


# --- persistence & purity -------------------------------------------------------------------------

def test_conversation_survives_json_round_trip_mid_flow():
    bot = Bot()
    for cid in ["order", "cat:pizze", "item:marg", "custom", "opt:maxi"]:
        bot.press(cid)
    bot.conv = Conversation.model_validate_json(bot.conv.model_dump_json())
    bot.press("opt:classico")
    assert bot.conv.data["group_index"] == 2


def test_handle_does_not_mutate_input():
    bot = Bot()
    before = bot.conv.model_dump()
    handle(bot.conv, Choice("order"), bot.ctx)
    assert bot.conv.model_dump() == before


def test_cart_lines_cannot_carry_prices():
    with pytest.raises(ValidationError):
        Conversation(restaurant_id="r", channel="telegram", customer_ref="1",
                     cart=[{"menu_item_id": "marg", "quantity": 1, "price": "0.01"}])


# --- slots & menu ---------------------------------------------------------------------------------------

def test_slots_respect_lead_time_grid_and_capacity():
    got = slots.available_slots(SETTINGS, at(19), booked={at(19, 45): 4}, limit=3)
    assert got == [at(19, 30), at(20), at(20, 15)]


def test_slots_need_aware_now():
    with pytest.raises(ValueError):
        slots.available_slots(SETTINGS, datetime(2026, 10, 15, 19))


def test_menu_rejects_unknown_references():
    with pytest.raises(MenuError):
        Menu(categories=(), items=(MenuItem("x", "nope", "X", D("1")),))

# tests/test_order_schema.py — PizzaBot Sprint 1 domain models

from datetime import datetime, timezone
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from pizzabot.order import (
    MAX_KITCHEN_NOTES, VAT_RATE, CartItem, CartOption, Fulfilment, Order,
    OrderStatus,
)


# --- enum surface -----------------------------------------------------------

def test_order_status_enum_values():
    assert {s.value for s in OrderStatus} == {
        "pending", "confirmed", "in_kitchen", "ready",
        "delivered", "cancelled",
    }


# --- CartItem ---------------------------------------------------------------

def test_cart_item_valid():
    item = CartItem(menu_item_id="m1", dish_name="Margherita", quantity=2, unit_price_eur="8.5")
    assert item.dish_name == "Margherita"
    assert item.quantity == 2
    assert item.unit_price_eur == D("8.50")
    assert isinstance(item.unit_price_eur, D)


@pytest.mark.parametrize("raw", [8.5, "8.5", "8.50", D("8.5")])
def test_cart_item_unit_price_canonicalized_to_cents(raw):
    item = CartItem(menu_item_id="m1", dish_name="Margherita", quantity=1, unit_price_eur=raw)
    assert str(item.unit_price_eur) == "8.50"


def test_cart_item_float_price_is_exact_not_binary():
    # Floats go through str(), so 0.1 is exactly 0.10 — not 0.1000000000000000055…
    item = CartItem(menu_item_id="m1", dish_name="Bibita", quantity=1, unit_price_eur=0.1)
    assert item.unit_price_eur == D("0.10")


@pytest.mark.parametrize("bad_quantity", [0, -1, -10])
def test_cart_item_invalid_quantity(bad_quantity):
    with pytest.raises(ValidationError):
        CartItem(menu_item_id="m1", dish_name="Margherita", quantity=bad_quantity,
                 unit_price_eur="8.5")


@pytest.mark.parametrize("bad_price", ["-0.01", -1.0, -50])
def test_cart_item_invalid_unit_price(bad_price):
    with pytest.raises(ValidationError):
        CartItem(menu_item_id="m1", dish_name="Margherita", quantity=1,
                 unit_price_eur=bad_price)


@pytest.mark.parametrize("bad_price", ["8.505", 0.123, "nan", "inf", "abc"])
def test_cart_item_rejects_sub_cent_and_non_finite_prices(bad_price):
    with pytest.raises(ValidationError):
        CartItem(menu_item_id="m1", dish_name="Margherita", quantity=1,
                 unit_price_eur=bad_price)


def test_cart_item_subtotal_computed():
    item = CartItem(menu_item_id="m1", dish_name="Diavola", quantity=3, unit_price_eur="10")
    assert item.subtotal_eur == D("30.00")


def test_cart_item_subtotal_has_no_float_drift():
    # 0.1 * 3 in float is 0.30000000000000004 — Decimal gives exactly 0.30.
    item = CartItem(menu_item_id="m1", dish_name="Bibita", quantity=3, unit_price_eur=0.1)
    assert item.subtotal_eur == D("0.30")
    assert str(item.subtotal_eur) == "0.30"


def test_cart_item_blank_dish_name_rejected():
    with pytest.raises(ValidationError):
        CartItem(menu_item_id="m1", dish_name="   ", quantity=1, unit_price_eur="5")


# --- Order ------------------------------------------------------------------

def _items():
    return [
        CartItem(menu_item_id="m1", dish_name="Margherita", quantity=2, unit_price_eur="8.50"),
        CartItem(menu_item_id="m1", dish_name="Diavola", quantity=1, unit_price_eur="10.00"),
    ]


def _order(**kw):
    base = dict(
        order_id="ORD-001",
        restaurant_id="rest-monza-01",
        channel="telegram",
        customer_ref="123456789",
        items=_items(),
    )
    base.update(kw)
    return Order(**base)


def test_order_valid_defaults_total_and_status():
    order = _order()
    assert order.status is OrderStatus.pending
    # 2*8.50 + 1*10.00 = 27.00
    assert order.total_eur == D("27.00")
    assert isinstance(order.total_eur, D)


def test_order_explicit_total_matching_computed_accepted():
    order = _order(total_eur="27.00")
    assert order.total_eur == D("27.00")


@pytest.mark.parametrize("wrong_total", ["99.00", "27.01", "26.99"])
def test_order_explicit_total_mismatch_rejected(wrong_total):
    # Money never comes from model text: even a 1-cent drift is rejected.
    with pytest.raises(ValidationError):
        _order(total_eur=wrong_total)


def test_order_requires_at_least_one_item():
    with pytest.raises(ValidationError):
        _order(items=[])


def test_order_created_at_is_timezone_aware_utc():
    before = datetime.now(timezone.utc)
    order = _order()
    after = datetime.now(timezone.utc)
    assert order.created_at.tzinfo is not None
    assert before <= order.created_at <= after


def test_order_status_can_be_overridden():
    order = _order(status=OrderStatus.in_kitchen)
    assert order.status is OrderStatus.in_kitchen


def test_order_rejects_unknown_fields():
    # extra='forbid' guards against silent typos at the API boundary.
    with pytest.raises(ValidationError):
        _order(currency="EUR")  # not part of the contract


# --- restaurant_id (multi-tenant) -------------------------------------------

def test_order_requires_restaurant_id():
    with pytest.raises(ValidationError):
        Order(order_id="ORD-010", channel="telegram",
              customer_ref="123456789", items=_items())


@pytest.mark.parametrize("bad", ["", "   "])
def test_order_blank_restaurant_id_rejected(bad):
    with pytest.raises(ValidationError):
        _order(restaurant_id=bad)


def test_order_restaurant_id_is_stripped():
    assert _order(restaurant_id="  rest-1  ").restaurant_id == "rest-1"


# --- IVA (prices are IVA-inclusive; VAT is extracted from the total) --------

def test_default_vat_rate_is_22_percent():
    assert VAT_RATE == D("0.22")
    assert _order().vat_rate == D("0.22")


def test_order_vat_breakdown_22():
    order = _order()
    # 27.00 / 1.22 = 22.1311… → imponibile 22.13, IVA 4.87
    assert order.vat_eur == D("4.87")
    assert order.net_eur == D("22.13")


@pytest.mark.parametrize("price", ["0.01", "1.00", "9.99", "10.00",
                                   "12.20", "33.33", "999.99"])
def test_net_plus_vat_equals_total_exactly(price):
    order = _order(items=[CartItem(menu_item_id="m1", dish_name="X", quantity=1,
                                   unit_price_eur=price)])
    assert order.net_eur + order.vat_eur == order.total_eur
    # Both parts are whole cents.
    assert order.vat_eur == order.vat_eur.quantize(D("0.01"))
    assert order.net_eur == order.net_eur.quantize(D("0.01"))


def test_order_vat_on_round_imponibile():
    # 12.20 gross at 22% → exactly 10.00 net + 2.20 IVA.
    order = _order(items=[CartItem(menu_item_id="m1", dish_name="X", quantity=1,
                                   unit_price_eur="12.20")])
    assert order.net_eur == D("10.00")
    assert order.vat_eur == D("2.20")


def test_order_vat_rate_override():
    order = _order(
        items=[CartItem(menu_item_id="m1", dish_name="X", quantity=1, unit_price_eur="11.00")],
        vat_rate="0.10",
    )
    assert order.vat_eur == D("1.00")
    assert order.net_eur == D("10.00")


def test_order_zero_total_has_zero_vat():
    order = _order(items=[CartItem(menu_item_id="m1", dish_name="Acqua omaggio", quantity=1,
                                   unit_price_eur="0")])
    assert order.total_eur == D("0.00")
    assert order.vat_eur == D("0.00")
    assert order.net_eur == D("0.00")


@pytest.mark.parametrize("bad_rate", ["-0.01", "1.5"])
def test_order_vat_rate_out_of_range_rejected(bad_rate):
    with pytest.raises(ValidationError):
        _order(vat_rate=bad_rate)


# --- serialization ----------------------------------------------------------

def test_model_dump_safe_serializes_money_as_exact_strings():
    data = _order().model_dump_safe()
    assert data["status"] == "pending"
    assert data["restaurant_id"] == "rest-monza-01"
    assert data["total_eur"] == "27.00"
    assert data["vat_eur"] == "4.87"
    assert data["net_eur"] == "22.13"
    assert data["items"][0]["unit_price_eur"] == "8.50"
    assert data["items"][0]["subtotal_eur"] == "17.00"


def test_order_round_trips_through_json_without_computed_fields():
    # Computed fields are outputs only; extra='forbid' rejects them as input,
    # so storage must dump with round_trip-safe excludes.
    order = _order()
    payload = order.model_dump_json(
        exclude={"vat_eur": True, "net_eur": True, "items_total_eur": True,
                 "items": {"__all__": {"subtotal_eur"}}}
    )
    assert Order.model_validate_json(payload) == order


def test_order_rejects_computed_fields_as_input():
    with pytest.raises(ValidationError):
        _order(vat_eur="0.00")


# --- options, fulfilment, slot, notes ---------------------------------------

def test_cart_option_allows_negative_delta():
    opt = CartOption(option_id="baby", name="Baby", price_delta_eur="-1")
    assert opt.price_delta_eur == D("-1.00")


def test_cart_item_carries_option_snapshot():
    item = CartItem(
        menu_item_id="m-marg", dish_name="Margherita", quantity=1,
        unit_price_eur="10.00",
        options=[CartOption(option_id="maxi", name="Maxi",
                            price_delta_eur="4.00")],
    )
    assert item.options[0].name == "Maxi"


def test_order_id_generated_when_missing():
    a = Order(restaurant_id="r", channel="telegram", customer_ref="1",
              items=_items())
    b = Order(restaurant_id="r", channel="telegram", customer_ref="1",
              items=_items())
    assert a.order_id and a.order_id != b.order_id


def test_unknown_channel_rejected():
    with pytest.raises(ValidationError):
        _order(channel="sms")


def test_delivery_requires_address():
    with pytest.raises(ValidationError):
        _order(fulfilment="delivery")
    with pytest.raises(ValidationError):
        _order(fulfilment="delivery", address="   ")


def test_delivery_total_includes_fee():
    order = _order(fulfilment="delivery", address="Via Roma 1, 20900 Monza",
                   delivery_fee_eur="2.50")
    assert order.fulfilment is Fulfilment.delivery
    assert order.items_total_eur == D("27.00")
    assert order.total_eur == D("29.50")
    assert order.net_eur + order.vat_eur == D("29.50")


def test_explicit_total_must_include_fee():
    with pytest.raises(ValidationError):
        _order(fulfilment="delivery", address="Via Roma 1",
               delivery_fee_eur="2.50", total_eur="27.00")


def test_pickup_rejects_delivery_fee():
    with pytest.raises(ValidationError):
        _order(delivery_fee_eur="2.50")


def test_slot_must_be_timezone_aware():
    with pytest.raises(ValidationError):
        _order(slot_at=datetime(2026, 10, 15, 20, 0))
    order = _order(slot_at=datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc))
    assert order.slot_at.tzinfo is not None


def test_kitchen_notes_cleaned():
    order = _order(kitchen_notes="  senza cipolla\x00\x1b[31m\nben cotta  ")
    assert order.kitchen_notes == "senza cipolla[31m\nben cotta"
    assert _order(kitchen_notes="   ").kitchen_notes is None


def test_kitchen_notes_length_capped():
    _order(kitchen_notes="x" * MAX_KITCHEN_NOTES)
    with pytest.raises(ValidationError):
        _order(kitchen_notes="x" * (MAX_KITCHEN_NOTES + 1))


# --- import / model stability regression -----------------------------------

def test_public_surface_stable():
    # Locks the exported names so accidental removals trip a test.
    from pizzabot.order import schema as s
    assert hasattr(s, "CartItem")
    assert hasattr(s, "Order")
    assert hasattr(s, "OrderStatus")
    assert hasattr(s, "VAT_RATE")
    assert set(CartItem.model_fields) == {
        "menu_item_id", "dish_name", "options", "quantity", "unit_price_eur"
    }
    assert set(Order.model_fields) == {
        "order_id", "restaurant_id", "channel", "customer_ref", "items",
        "fulfilment", "address", "slot_at", "kitchen_notes",
        "delivery_fee_eur", "status", "vat_rate", "total_eur", "created_at",
    }
    assert set(Order.model_computed_fields) == {
        "items_total_eur", "vat_eur", "net_eur"
    }

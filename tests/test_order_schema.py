# tests/test_pizza_order_schema.py — PizzaBot Sprint 1 domain models

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from pizzabot.order import CartItem, Order, OrderStatus


# --- enum surface -----------------------------------------------------------

def test_order_status_enum_values():
    assert {s.value for s in OrderStatus} == {
        "pending", "confirmed", "in_kitchen", "ready",
        "delivered", "cancelled",
    }


# --- CartItem ---------------------------------------------------------------

def test_cart_item_valid():
    item = CartItem(dish_name="Margherita", quantity=2, unit_price_eur=8.5)
    assert item.dish_name == "Margherita"
    assert item.quantity == 2
    assert item.unit_price_eur == 8.5


@pytest.mark.parametrize("bad_quantity", [0, -1, -10])
def test_cart_item_invalid_quantity(bad_quantity):
    with pytest.raises(ValidationError):
        CartItem(dish_name="Margherita", quantity=bad_quantity,
                 unit_price_eur=8.5)


@pytest.mark.parametrize("bad_price", [-0.01, -1.0, -50])
def test_cart_item_invalid_unit_price(bad_price):
    with pytest.raises(ValidationError):
        CartItem(dish_name="Margherita", quantity=1,
                 unit_price_eur=bad_price)


def test_cart_item_subtotal_computed():
    item = CartItem(dish_name="Diavola", quantity=3, unit_price_eur=10.0)
    assert item.subtotal_eur == 30.0


def test_cart_item_subtotal_rounds_to_two_decimals():
    # 0.1 * 3 in float is 0.30000000000000004 — must round cleanly to 0.30.
    item = CartItem(dish_name="Bibita", quantity=3, unit_price_eur=0.1)
    assert item.subtotal_eur == 0.30


def test_cart_item_blank_dish_name_rejected():
    with pytest.raises(ValidationError):
        CartItem(dish_name="   ", quantity=1, unit_price_eur=5.0)


# --- Order ------------------------------------------------------------------

def _items():
    return [
        CartItem(dish_name="Margherita", quantity=2, unit_price_eur=8.5),
        CartItem(dish_name="Diavola", quantity=1, unit_price_eur=10.0),
    ]


def test_order_valid_defaults_total_and_status():
    order = Order(
        order_id="ORD-001",
        customer_phone="+393331234567",
        items=_items(),
    )
    assert order.status is OrderStatus.pending
    # 2*8.5 + 1*10.0 = 27.0
    assert order.total_eur == 27.0


def test_order_explicit_total_matching_computed_accepted():
    order = Order(
        order_id="ORD-002",
        customer_phone="+393331234567",
        items=_items(),
        total_eur=27.0,
    )
    assert order.total_eur == 27.0


def test_order_explicit_total_mismatch_rejected():
    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-003",
            customer_phone="+393331234567",
            items=_items(),
            total_eur=99.0,
        )


def test_order_requires_at_least_one_item():
    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-004",
            customer_phone="+393331234567",
            items=[],
        )


def test_order_created_at_is_timezone_aware_utc():
    before = datetime.now(timezone.utc)
    order = Order(
        order_id="ORD-005",
        customer_phone="+393331234567",
        items=_items(),
    )
    after = datetime.now(timezone.utc)
    assert order.created_at.tzinfo is not None
    assert before <= order.created_at <= after


def test_order_status_can_be_overridden():
    order = Order(
        order_id="ORD-006",
        customer_phone="+393331234567",
        items=_items(),
        status=OrderStatus.in_kitchen,
    )
    assert order.status is OrderStatus.in_kitchen


def test_order_rejects_unknown_fields():
    # extra='forbid' guards against silent typos at the API boundary.
    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-007",
            customer_phone="+393331234567",
            items=_items(),
            currency="EUR",  # not part of the contract
        )


# --- import / model stability regression -----------------------------------

def test_public_surface_stable():
    # Locks the exported names so accidental removals trip a test.
    from pizzabot.order import schema as s
    assert hasattr(s, "CartItem")
    assert hasattr(s, "Order")
    assert hasattr(s, "OrderStatus")
    assert set(CartItem.model_fields) == {
        "dish_name", "quantity", "unit_price_eur"
    }
    assert set(Order.model_fields) == {
        "order_id", "customer_phone", "items",
        "status", "total_eur", "created_at",
    }

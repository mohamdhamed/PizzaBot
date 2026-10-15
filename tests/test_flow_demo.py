# tests/test_flow_demo.py — المنيو التجريبي اللي بيستخدمه المحاكي

from datetime import datetime, timezone

from pizzabot.flow import Choice, Conversation, FlowContext, State, handle
from pizzabot.flow.demo import demo_menu, demo_settings


def test_demo_menu_is_consistent_and_every_category_is_visible():
    menu = demo_menu()
    assert [c.id for c in menu.active_categories()] == [c.id for c in menu.categories]


def test_demo_restaurant_accepts_orders_at_any_hour():
    ctx = FlowContext(demo_settings(), demo_menu(),
                      datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc))
    conv = Conversation(restaurant_id="demo", channel="telegram", customer_ref="1")
    assert handle(conv, Choice("order"), ctx).conversation.state is State.category

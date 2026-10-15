"""In-memory menu and restaurant settings for the guided flow.

Pure data + lookups, no I/O. The storage layer (PostgreSQL) will load rows
into these objects; the state machine only ever reads them. Prices are
IVA-inclusive ``Decimal`` and the single price rule lives here:

    unit price = base price + Σ price_delta of the chosen options
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal

from pizzabot.order.schema import VAT_RATE, round_eur


# EU Reg. 1169/2011, Annex II — numbered as on Italian menus.
ALLERGENS_IT: dict[int, str] = {
    1: "Glutine",
    2: "Crostacei",
    3: "Uova",
    4: "Pesce",
    5: "Arachidi",
    6: "Soia",
    7: "Latte",
    8: "Frutta a guscio",
    9: "Sedano",
    10: "Senape",
    11: "Sesamo",
    12: "Anidride solforosa e solfiti",
    13: "Lupini",
    14: "Molluschi",
}


class MenuError(ValueError):
    """Inconsistent menu data (unknown id, bad reference)."""


@dataclass(frozen=True)
class Option:
    id: str
    name: str
    price_delta_eur: Decimal = Decimal("0.00")
    allergens: frozenset[int] = frozenset()
    available: bool = True


@dataclass(frozen=True)
class OptionGroup:
    id: str
    name: str
    options: tuple[Option, ...]
    min_select: int = 1
    max_select: int = 1

    @property
    def is_single(self) -> bool:
        return self.max_select == 1

    def available_options(self) -> list[Option]:
        return [o for o in self.options if o.available]


@dataclass(frozen=True)
class MenuItem:
    id: str
    category_id: str
    name: str
    base_price_eur: Decimal
    description: str = ""
    allergens: frozenset[int] = frozenset()
    option_group_ids: tuple[str, ...] = ()
    available: bool = True
    age_restricted: bool = False


@dataclass(frozen=True)
class Category:
    id: str
    name: str


@dataclass(frozen=True)
class Menu:
    """Categories, items and option groups of one restaurant, in display order."""

    categories: tuple[Category, ...]
    items: tuple[MenuItem, ...]
    option_groups: tuple[OptionGroup, ...] = ()

    def __post_init__(self) -> None:
        cat_ids = {c.id for c in self.categories}
        group_ids = {g.id for g in self.option_groups}
        for item in self.items:
            if item.category_id not in cat_ids:
                raise MenuError(f"item {item.id}: unknown category {item.category_id}")
            for gid in item.option_group_ids:
                if gid not in group_ids:
                    raise MenuError(f"item {item.id}: unknown option group {gid}")

    # --- lookups -----------------------------------------------------------

    def category(self, category_id: str) -> Category | None:
        return next((c for c in self.categories if c.id == category_id), None)

    def item(self, item_id: str) -> MenuItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def group(self, group_id: str) -> OptionGroup | None:
        return next((g for g in self.option_groups if g.id == group_id), None)

    def option(self, option_id: str) -> tuple[OptionGroup, Option] | None:
        for g in self.option_groups:
            for o in g.options:
                if o.id == option_id:
                    return g, o
        return None

    # --- what the customer can see -----------------------------------------

    def available_items(self, category_id: str) -> list[MenuItem]:
        return [i for i in self.items if i.category_id == category_id and i.available]

    def active_categories(self) -> list[Category]:
        """Categories with at least one available item."""
        return [c for c in self.categories if self.available_items(c.id)]

    def groups_for(self, item: MenuItem) -> list[OptionGroup]:
        """Option groups an item offers that still have something to pick."""
        groups = [self.group(gid) for gid in item.option_group_ids]
        return [g for g in groups if g is not None and g.available_options()]

    # --- pricing -----------------------------------------------------------

    def unit_price(self, item: MenuItem, option_ids: list[str]) -> Decimal:
        total = item.base_price_eur
        for oid in option_ids:
            found = self.option(oid)
            if found is None:
                raise MenuError(f"unknown option {oid}")
            total += found[1].price_delta_eur
        return round_eur(total)

    def allergens_for(self, item: MenuItem, option_ids: list[str] = ()) -> list[int]:
        codes = set(item.allergens)
        for oid in option_ids:
            found = self.option(oid)
            if found is not None:
                codes |= found[1].allergens
        return sorted(codes)


@dataclass(frozen=True)
class RestaurantSettings:
    """Per-restaurant ordering rules. Times are local to ``timezone``."""

    restaurant_id: str
    name: str
    timezone: str = "Europe/Rome"
    vat_rate: Decimal = VAT_RATE
    pickup_enabled: bool = True
    delivery_enabled: bool = True
    delivery_fee_eur: Decimal = Decimal("0.00")
    min_order_eur: Decimal = Decimal("0.00")
    # Empty = deliver anywhere. Otherwise the address must contain one of these CAP.
    delivery_postcodes: frozenset[str] = frozenset()
    # weekday (0 = Monday) → list of (opens, closes). Several ranges per day allowed.
    opening_hours: dict[int, tuple[tuple[time, time], ...]] = field(default_factory=dict)
    slot_minutes: int = 15
    max_orders_per_slot: int = 4
    min_lead_minutes: int = 20

"""PizzaBot order domain models — pure Pydantic, no I/O.

This module owns the in-memory contract for cart items and orders. Storage
(Redis cart, PostgreSQL checkout) and messaging integrations live in
separate modules and consume these models.

Money is ``Decimal`` end to end, quantized to cents with half-up rounding.
Menu prices are IVA-inclusive (Italian B2C prices are shown "IVA inclusa"),
so the IVA on an order is extracted from the gross total (scorporo).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


_CENT = Decimal("0.01")

VAT_RATE = Decimal("0.22")


def round_eur(value: Decimal) -> Decimal:
    """يقرّب لـ سنتيّن مع half-up، عشان حسابات الفواتير الإيطالية."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


# Non-negative euro amount with at most 2 decimal places, always stored as
# a 2-place Decimal (8.5 → Decimal("8.50")). Floats are accepted via their
# str() form, so 0.1 becomes Decimal("0.10"), not the binary approximation.
Eur = Annotated[
    Decimal,
    Field(ge=0, max_digits=9, decimal_places=2),
    AfterValidator(round_eur),
]


def _strip_required(v: str, name: str) -> str:
    stripped = v.strip()
    if not stripped:
        raise ValueError(f"{name} must not be blank")
    return stripped


class OrderStatus(str, Enum):
    """Lifecycle of a pizzeria order. Linear in the happy path; cancelled
    is reachable from any non-terminal state."""

    pending = "pending"
    confirmed = "confirmed"
    in_kitchen = "in_kitchen"
    ready = "ready"
    delivered = "delivered"
    cancelled = "cancelled"


class CartItem(BaseModel):
    """One line on a pizzeria order. ``unit_price_eur`` is IVA-inclusive."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    dish_name: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    unit_price_eur: Eur

    @field_validator("dish_name")
    @classmethod
    def _strip_dish_name(cls, v: str) -> str:
        return _strip_required(v, "dish_name")

    @computed_field  # type: ignore[misc]
    @property
    def subtotal_eur(self) -> Decimal:
        return round_eur(self.quantity * self.unit_price_eur)


class Order(BaseModel):
    """A confirmed or in-flight pizzeria order, scoped to one restaurant."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(..., min_length=1)
    restaurant_id: str = Field(..., min_length=1)
    customer_phone: str = Field(..., min_length=5)
    items: list[CartItem] = Field(..., min_length=1)
    status: OrderStatus = OrderStatus.pending
    vat_rate: Decimal = Field(default=VAT_RATE, ge=0, le=1)
    total_eur: Eur | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("restaurant_id")
    @classmethod
    def _strip_restaurant_id(cls, v: str) -> str:
        return _strip_required(v, "restaurant_id")

    @field_validator("customer_phone")
    @classmethod
    def _strip_phone(cls, v: str) -> str:
        return _strip_required(v, "customer_phone")

    @model_validator(mode="after")
    def _resolve_total(self) -> "Order":
        computed = round_eur(sum((i.subtotal_eur for i in self.items), Decimal(0)))
        if self.total_eur is not None and self.total_eur != computed:
            raise ValueError(
                f"total_eur={self.total_eur} does not match sum of item "
                f"subtotals={computed}"
            )
        object.__setattr__(self, "total_eur", computed)
        return self

    @computed_field  # type: ignore[misc]
    @property
    def vat_eur(self) -> Decimal:
        """IVA contained in the gross total: total - total / (1 + rate)."""
        assert self.total_eur is not None
        return round_eur(self.total_eur - self.total_eur / (1 + self.vat_rate))

    @computed_field  # type: ignore[misc]
    @property
    def net_eur(self) -> Decimal:
        """Imponibile. Derived as total - vat so net + vat == total exactly."""
        assert self.total_eur is not None
        return self.total_eur - self.vat_eur

    def model_dump_safe(self) -> dict[str, Any]:
        """JSON-ready dump: enum as its value, money as exact strings ("27.00")."""
        return self.model_dump(mode="json")

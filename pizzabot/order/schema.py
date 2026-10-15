"""PizzaBot order domain models — pure Pydantic, no I/O.

This module owns the in-memory contract for cart items and orders. Storage
(Redis cart, PostgreSQL checkout) and messaging integrations live in
separate modules and consume these models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


_TOTAL_TOLERANCE_EUR = Decimal("0.01")


def _round_eur(value: float) -> float:
    """يقرّب لـ سنتيّن decimal مع half-up، عشان حسابات الفواتير الإيطالية."""
    quant = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(quant)


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
    """One line on a pizzeria order."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    dish_name: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    unit_price_eur: float = Field(..., ge=0)

    @field_validator("dish_name")
    @classmethod
    def _strip_dish_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("dish_name must not be blank")
        return stripped

    @computed_field  # type: ignore[misc]
    @property
    def subtotal_eur(self) -> float:
        return _round_eur(self.quantity * self.unit_price_eur)


class Order(BaseModel):
    """A confirmed or in-flight pizzeria order."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(..., min_length=1)
    customer_phone: str = Field(..., min_length=5)
    items: list[CartItem] = Field(..., min_length=1)
    status: OrderStatus = OrderStatus.pending
    total_eur: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("customer_phone")
    @classmethod
    def _strip_phone(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("customer_phone must not be blank")
        return stripped

    @model_validator(mode="after")
    def _resolve_total(self) -> "Order":
        computed = _round_eur(sum(item.subtotal_eur for item in self.items))
        if self.total_eur is None:
            object.__setattr__(self, "total_eur", computed)
            return self
        provided = Decimal(str(self.total_eur))
        if abs(provided - Decimal(str(computed))) > _TOTAL_TOLERANCE_EUR:
            raise ValueError(
                f"total_eur={self.total_eur} does not match sum of item "
                f"subtotals={computed}"
            )
        # Canonicalize to the rounded computed value.
        object.__setattr__(self, "total_eur", computed)
        return self

    def model_dump_safe(self) -> dict[str, Any]:
        """Convenience JSON-ready dump that serializes the enum as its value."""
        return self.model_dump(mode="json")

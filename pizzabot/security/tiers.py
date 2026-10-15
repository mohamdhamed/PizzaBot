# tiers.py
# تعريف مستويات الوصول وحدودها

from dataclasses import dataclass
from typing import Literal

Tier = Literal["anonymous", "lead", "agent"]

DEFAULT_TIER: Tier = "anonymous"
TIER_HEADER = "X-Tier"


@dataclass(frozen=True)
class TierLimits:
    rate_per_hour: int       # عدد الأسئلة المسموحة في الساعة
    rate_per_minute: int     # حد للـ burst
    max_top_k: int           # الحد الأقصى للنتائج
    max_question_length: int # طول السؤال بالحروف
    show_full_pii: bool      # هل يرى الأرقام كاملة؟


TIERS: dict[Tier, TierLimits] = {
    "anonymous": TierLimits(
        rate_per_hour=20,
        rate_per_minute=5,
        max_top_k=4,
        max_question_length=400,
        show_full_pii=False,
    ),
    "lead": TierLimits(
        rate_per_hour=100,
        rate_per_minute=15,
        max_top_k=8,
        max_question_length=800,
        show_full_pii=False,   # يشوف آخر 4 أرقام بس
    ),
    "agent": TierLimits(
        rate_per_hour=1000,
        rate_per_minute=60,
        max_top_k=20,
        max_question_length=2000,
        show_full_pii=True,
    ),
}


def resolve_tier(header_value: str | None) -> Tier:
    """يحدد الـ tier من header request — يرجع DEFAULT_TIER لو غير صالح"""
    if not header_value:
        return DEFAULT_TIER
    candidate = header_value.strip().lower()
    if candidate in TIERS:
        return candidate  # type: ignore[return-value]
    return DEFAULT_TIER


def limits_for(tier: Tier) -> TierLimits:
    return TIERS[tier]

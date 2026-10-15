# pii_masker.py
# إخفاء أرقام الموبايل والإيميلات حسب الـ tier

import re
from typing import Any
from pizzabot.security.tiers import Tier, limits_for

# أرقام موبايل (مصري + سعودي + دولي عام)
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-]?)?(?:\d[\s\-]?){7,14}\d")
_EMAIL_RE = re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+")


def _mask_phone(match: re.Match, *, keep_last: int = 0) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 7:
        return match.group(0)  # مش رقم موبايل صالح
    if keep_last > 0 and keep_last < len(digits):
        return "*" * (len(digits) - keep_last) + digits[-keep_last:]
    return "*" * len(digits)


def _mask_email(match: re.Match) -> str:
    s = match.group(0)
    name, _, domain = s.partition("@")
    if len(name) <= 2:
        masked_name = "*" * len(name)
    else:
        masked_name = name[0] + "*" * (len(name) - 1)
    return f"{masked_name}@{domain}"


def mask_text(text: str, tier: Tier) -> str:
    """يخفي PII في نص — مستوى الإخفاء حسب الـ tier"""
    limits = limits_for(tier)
    if limits.show_full_pii:
        return text  # agent — يشوف كل حاجة

    keep = 4 if tier == "lead" else 0
    masked = _PHONE_RE.sub(lambda m: _mask_phone(m, keep_last=keep), text)
    masked = _EMAIL_RE.sub(_mask_email, masked)
    return masked


_PII_KEYS = {"agent_phone", "phone", "mobile", "email", "agent_email"}


def mask_dict(data: dict[str, Any], tier: Tier) -> dict[str, Any]:
    """يخفي PII في dict (recursive) — يخفي القيم في حقول معروفة + مسح النصوص"""
    if limits_for(tier).show_full_pii:
        return data
    return _mask_value(data, tier)


def _mask_value(value: Any, tier: Tier, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _mask_value(v, tier, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_value(v, tier) for v in value]
    if isinstance(value, str):
        # حقول الـ PII الصريحة بتتقص بسرعة
        if key.lower() in _PII_KEYS:
            keep = 4 if tier == "lead" else 0
            digits = re.sub(r"\D", "", value)
            if digits and keep > 0 and keep < len(digits):
                return "*" * (len(digits) - keep) + digits[-keep:]
            return "*" * max(len(value), 4)
        return mask_text(value, tier)
    return value

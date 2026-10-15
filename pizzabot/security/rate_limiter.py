# rate_limiter.py
# Rate limiter بسيط في الذاكرة — per (tier, IP) buckets
# بنستخدم sliding window: deque بتحتفظ بـ timestamps خلال آخر دقيقة/ساعة

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Iterable

from fastapi import HTTPException, Request

from pizzabot.security.tiers import resolve_tier, limits_for, TIER_HEADER, Tier


_HOUR = 3600.0
_MINUTE = 60.0


class _SlidingWindowCounter:
    """sliding window counter بـ deque per key + lock للـ thread-safety"""

    def __init__(self):
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, *, window: float, limit: int) -> bool:
        """يضيف timestamp ويرجع True لو لسه جوا الحد، False لو تجاوز"""
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            q = self._buckets[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_counter = _SlidingWindowCounter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> Tier:
    """
    FastAPI dependency — يفحص rate limits ويرفع 429 لو اتجاوز،
    ويرجع الـ tier للاستخدام في باقي الـ endpoint.
    """
    tier = resolve_tier(request.headers.get(TIER_HEADER))
    limits = limits_for(tier)
    ip = _client_ip(request)
    base_key = f"{tier}:{ip}"

    # نشيك على الحدين (الدقيقة قبل الساعة عشان الفشل أوضح)
    if not _counter.hit(f"{base_key}:min", window=_MINUTE,
                         limit=limits.rate_per_minute):
        raise HTTPException(
            status_code=429,
            detail=f"تجاوزت الحد {limits.rate_per_minute} طلب/دقيقة لـ tier '{tier}'",
        )
    if not _counter.hit(f"{base_key}:hr", window=_HOUR,
                         limit=limits.rate_per_hour):
        raise HTTPException(
            status_code=429,
            detail=f"تجاوزت الحد {limits.rate_per_hour} طلب/ساعة لـ tier '{tier}'",
        )

    return tier


def reset_rate_limits() -> None:
    """للاستخدام في الـ tests"""
    _counter.reset()

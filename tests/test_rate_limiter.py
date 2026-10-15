# tests/test_rate_limiter.py — rate limit لكل (مطعم، مرسل)

import pytest

from pizzabot.security.rate_limiter import allow_sender, reset_rate_limits
from pizzabot.security.tiers import limits_for


@pytest.fixture(autouse=True)
def _clean():
    reset_rate_limits()
    yield
    reset_rate_limits()


def test_sender_blocked_after_minute_limit():
    limit = limits_for("anonymous").rate_per_minute
    for _ in range(limit):
        assert allow_sender("rest-1", "+393331111111")
    assert not allow_sender("rest-1", "+393331111111")


def test_senders_have_separate_buckets():
    limit = limits_for("anonymous").rate_per_minute
    for _ in range(limit):
        allow_sender("rest-1", "+393331111111")
    # عميل تاني في نفس المطعم مش متأثر — رغم إن الاتنين جايين من نفس IP بتاع Meta
    assert allow_sender("rest-1", "+393332222222")


def test_same_sender_separate_per_restaurant():
    limit = limits_for("anonymous").rate_per_minute
    for _ in range(limit):
        allow_sender("rest-1", "+393331111111")
    assert allow_sender("rest-2", "+393331111111")


@pytest.mark.parametrize("rid,sid", [("", "x"), ("r", "")])
def test_missing_ids_rejected(rid, sid):
    with pytest.raises(ValueError):
        allow_sender(rid, sid)

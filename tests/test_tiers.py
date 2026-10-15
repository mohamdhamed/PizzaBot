# tests/test_tiers.py — تعريف الـ tiers

import pytest
from pizzabot.security.tiers import resolve_tier, limits_for, TIERS, DEFAULT_TIER


def test_default_when_header_missing():
    assert resolve_tier(None) == DEFAULT_TIER
    assert resolve_tier("") == DEFAULT_TIER


def test_resolve_known_tiers():
    assert resolve_tier("anonymous") == "anonymous"
    assert resolve_tier("lead") == "lead"
    assert resolve_tier("agent") == "agent"


def test_case_insensitive():
    assert resolve_tier("AGENT") == "agent"
    assert resolve_tier("Lead") == "lead"


def test_unknown_falls_back_to_default():
    assert resolve_tier("admin") == DEFAULT_TIER
    assert resolve_tier("../etc/passwd") == DEFAULT_TIER


def test_all_tiers_have_limits():
    for tier in TIERS:
        l = limits_for(tier)
        assert l.rate_per_hour > 0
        assert l.max_top_k > 0


def test_agent_has_more_than_lead_more_than_anonymous():
    a = limits_for("anonymous")
    l = limits_for("lead")
    g = limits_for("agent")
    assert a.rate_per_hour < l.rate_per_hour < g.rate_per_hour
    assert a.max_top_k <= l.max_top_k <= g.max_top_k
    assert g.show_full_pii is True
    assert a.show_full_pii is False

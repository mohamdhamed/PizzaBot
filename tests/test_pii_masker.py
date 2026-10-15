# tests/test_pii_masker.py — إخفاء PII

import pytest
from pizzabot.security.pii_masker import mask_text, mask_dict


class TestMaskText:
    def test_anonymous_full_mask(self):
        result = mask_text("اتصل على 01001234567", "anonymous")
        assert "01001234567" not in result
        assert "0100" not in result

    def test_lead_keeps_last_4(self):
        result = mask_text("اتصل على 01001234567", "lead")
        assert "4567" in result
        assert "0100" not in result

    def test_agent_no_mask(self):
        text = "اتصل على 01001234567"
        assert mask_text(text, "agent") == text

    def test_email_anonymous(self):
        result = mask_text("راسلني على ahmed@example.com", "anonymous")
        assert "ahmed" not in result
        assert "@example.com" in result

    def test_email_agent(self):
        text = "راسلني على ahmed@example.com"
        assert mask_text(text, "agent") == text

    def test_no_phone_unchanged(self):
        text = "أنهي شقة في التجمع؟"
        assert mask_text(text, "anonymous") == text


class TestMaskDict:
    def test_explicit_phone_field_anonymous(self):
        data = {"agent_phone": "01001234567", "name": "أحمد"}
        result = mask_dict(data, "anonymous")
        assert "01001234567" not in result["agent_phone"]
        assert result["name"] == "أحمد"

    def test_explicit_phone_field_lead(self):
        data = {"agent_phone": "01001234567"}
        result = mask_dict(data, "lead")
        assert result["agent_phone"].endswith("4567")

    def test_agent_unchanged(self):
        data = {"agent_phone": "01001234567", "agent_email": "x@y.com"}
        assert mask_dict(data, "agent") == data

    def test_nested_structures(self):
        data = {
            "sources": [
                {"agent_phone": "01001234567", "preview": "اتصل 01001234567"},
                {"agent_phone": "01007654321", "preview": "OK"},
            ]
        }
        result = mask_dict(data, "anonymous")
        assert "01001234567" not in result["sources"][0]["agent_phone"]
        assert "01001234567" not in result["sources"][0]["preview"]
        assert "01007654321" not in result["sources"][1]["agent_phone"]

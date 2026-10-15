# tests/test_menu_normalizer.py — منقول من Madaar (TestPizzaCanonicalFields / TestParseBool / TestNormalizePizzaDataframe)

import pandas as pd
from decimal import Decimal

import pytest

from pizzabot.menu.normalizer import (
    match_column, normalize_dataframe, parse_bool, parse_number,
)


class TestColumnMatching:
    @pytest.mark.parametrize("name,expected", [
        ("dish_name", "dish_name"),
        ("nome", "dish_name"),
        ("piatto", "dish_name"),
        ("اسم الطبق", "dish_name"),
        ("prezzo", "price_eur"),
        ("price_eur", "price_eur"),
        ("price", "price_eur"),
        ("سعر اليورو", "price_eur"),
        ("category", "category"),
        ("categoria", "category"),
        ("tipologia", "category"),
        ("التصنيف", "category"),
        ("available", "available"),
        ("disponibile", "available"),
        ("in_stock", "available"),
        ("متوفر", "available"),
        ("ingredients", "ingredients"),
        ("ingredienti", "ingredients"),
        ("المكونات", "ingredients"),
        ("Nome Piatto", "dish_name"),
    ])
    def test_aliases(self, name, expected):
        assert match_column(name) == expected

    def test_unknown_returns_none(self):
        assert match_column("allergeni_extra") is None


class TestParseNumber:
    @pytest.mark.parametrize("value,expected", [
        ("8.50", 8.5),
        ("€ 10", 10.0),
        ("12 EUR", 12.0),
        ("", None),
        ("nan", None),
        ("abc", None),
    ])
    def test_parse(self, value, expected):
        assert parse_number(value) == expected

    @pytest.mark.parametrize("value,expected", [
        # الصيغة الإيطالية: الفاصلة عشرية والنقطة للآلاف
        ("8,50", "8.50"),
        ("€ 8,50", "8.50"),
        ("8,50 €", "8.50"),
        ("1,5", "1.5"),
        ("1.200,00", "1200.00"),
        ("1.200", "1200"),
        ("1.234.567", "1234567"),
        # الصيغة الإنجليزية
        ("1,200.50", "1200.50"),
        ("-3,5", "-3.5"),
    ])
    def test_parse_italian_and_english_separators(self, value, expected):
        assert parse_number(value) == Decimal(expected)

    @pytest.mark.parametrize("value,expected", [
        (8.5, "8.5"),
        (0.333, "0.333"),   # أرقام Excel ما بيتعملهاش تخمين فواصل
        (12, "12"),
    ])
    def test_numeric_cells_are_not_reinterpreted(self, value, expected):
        assert parse_number(value) == Decimal(expected)

    @pytest.mark.parametrize("value", [None, float("nan"), "-", ".", "€", True])
    def test_unparseable_returns_none(self, value):
        assert parse_number(value) is None

    def test_returns_decimal(self):
        assert isinstance(parse_number("8,50"), Decimal)


class TestParseBool:
    @pytest.mark.parametrize("value,expected", [
        (True, True),
        (False, False),
        ("yes", True),
        ("no", False),
        ("si", True),
        ("sì", True),
        ("disponibile", True),
        ("non disponibile", False),
        ("متوفر", True),
        ("غير متوفر", False),
        ("نعم", True),
        ("لا", False),
        (1, True),
        (0, False),
        ("1", True),
        ("0", False),
        (None, None),
        ("", None),
        ("nan", None),
        ("maybe", None),
    ])
    def test_parse(self, value, expected):
        assert parse_bool(value) == expected


class TestNormalizeDataframe:
    def test_italian_row(self):
        df = pd.DataFrame([{
            "nome": "Margherita",
            "prezzo": "8.50",
            "categoria": "Pizza Classica",
            "disponibile": "si",
            "ingredienti": "pomodoro, mozzarella, basilico",
        }])
        out, unmapped = normalize_dataframe(df)
        assert unmapped == []
        row = out.iloc[0]
        assert row["dish_name"] == "Margherita"
        assert row["price_eur"] == 8.50
        assert row["category"] == "Pizza Classica"
        assert bool(row["available"]) is True
        assert row["ingredients"] == "pomodoro, mozzarella, basilico"

    def test_arabic_row(self):
        df = pd.DataFrame([{
            "اسم الطبق": "مارجريتا",
            "السعر باليورو": "8.5",
            "التصنيف": "بيتزا كلاسيك",
            "متوفر": "نعم",
            "المكونات": "طماطم، موتزاريلا، ريحان",
        }])
        out, _ = normalize_dataframe(df)
        row = out.iloc[0]
        assert row["dish_name"] == "مارجريتا"
        assert row["price_eur"] == 8.5
        assert bool(row["available"]) is True

    def test_unavailable_row(self):
        df = pd.DataFrame([{"nome": "Capricciosa", "disponibile": "no"}])
        out, _ = normalize_dataframe(df)
        assert bool(out.iloc[0]["available"]) is False

    def test_duplicate_price_columns_coalesce(self):
        df = pd.DataFrame([{"nome": "Marinara", "price": None, "prezzo": "7"}])
        out, _ = normalize_dataframe(df)
        assert list(out.columns).count("price_eur") == 1
        assert out.iloc[0]["price_eur"] == 7.0

    def test_unmapped_columns_kept(self):
        df = pd.DataFrame([{"nome": "Bufala", "allergeni_extra": "lattosio"}])
        out, unmapped = normalize_dataframe(df)
        assert unmapped == ["allergeni_extra"]
        assert out.iloc[0]["allergeni_extra"] == "lattosio"

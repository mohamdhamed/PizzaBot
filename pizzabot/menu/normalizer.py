# normalizer.py
# توحيد أعمدة ملف المنيو + تنظيف القيم (إيطالي / إنجليزي / عربي)
# منقول من Madaar (rag/normalizer.py) — الجزء الخاص بالبيتزا بس.

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

# -----------------------------------------------------------------------------
# Canonical schema — أي عمود في ملف المنيو بيتطابق على واحد من دول
# -----------------------------------------------------------------------------
CANONICAL_FIELDS: dict[str, list[str]] = {
    "dish_name":   ["dish_name", "nome", "nome_piatto", "piatto", "menu_item",
                    "item", "اسم الطبق", "الطبق"],
    "price_eur":   ["price_eur", "price", "prezzo", "prezzo_eur", "prezzo (eur)",
                    "eur", "السعر باليورو", "سعر اليورو", "السعر"],
    "category":    ["category", "categoria", "tipo", "tipologia",
                    "التصنيف", "الفئة", "الصنف"],
    "available":   ["available", "disponibile", "disponibilita",
                    "disponibilità", "in_stock", "stock",
                    "متوفر", "متاح"],
    "ingredients": ["ingredients", "ingredienti", "المكونات", "مكونات"],
}

_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias.lower().strip(): canonical
    for canonical, aliases in CANONICAL_FIELDS.items()
    for alias in aliases
}

_NUMERIC_FIELDS = {"price_eur"}
_BOOLEAN_FIELDS = {"available"}


# -----------------------------------------------------------------------------
# Column matching
# -----------------------------------------------------------------------------
def _normalize_key(s: str) -> str:
    """يوحد اسم العمود (lowercase, شيل المسافات والعلامات) للمطابقة"""
    s = str(s).lower().strip()
    s = re.sub(r"[\s_\-]+", "_", s)
    return s


def match_column(column_name: str) -> str | None:
    """يحاول يلاقي canonical field لاسم عمود معين. يرجع None لو فشل."""
    raw = column_name.lower().strip()
    if raw in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[raw]

    norm = _normalize_key(column_name)
    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        if _normalize_key(alias) == norm:
            return canonical
    return None


def map_columns(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """يرجع (mapping من العمود الأصلي → canonical, الأعمدة اللي مالقتش match)"""
    mapping: dict[str, str] = {}
    unmapped: list[str] = []
    for col in columns:
        canonical = match_column(col)
        if canonical:
            mapping[col] = canonical
        else:
            unmapped.append(col)
    return mapping, unmapped


# -----------------------------------------------------------------------------
# Value cleaning
# -----------------------------------------------------------------------------
_NULLISH = {"", "nan", "none", "null", "-", "n/a", "na", "غير محدد", "لا يوجد"}


def clean_value(value: Any) -> Any:
    """ينظف قيمة واحدة — يشيل المسافات، يحول null markers لـ None"""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None

    s = str(value).strip()
    if s.lower() in _NULLISH:
        return None
    return s


def _canonical_decimal_string(s: str) -> str:
    """يحوّل "1.200,50" / "1,200.50" / "8,50" لصيغة Decimal ("1200.50", "8.50").

    القاعدة: لو فيه "." و "," مع بعض، آخر واحد فيهم هو الفاصلة العشرية.
    لو فيه نوع واحد بس: لو اتكرر، أو جاله بعده 3 أرقام بالظبط ("1.200")،
    يبقى فاصل آلاف؛ غير كده يبقى فاصلة عشرية ("8,50" → 8.50).
    """
    if "." in s and "," in s:
        decimal_sep = "." if s.rfind(".") > s.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        return s.replace(thousands_sep, "").replace(decimal_sep, ".")

    for sep in (",", "."):
        if sep in s:
            head, _, tail = s.rpartition(sep)
            if s.count(sep) > 1 or len(tail) == 3:
                return s.replace(sep, "")
            return f"{head}.{tail}"
    return s


def parse_number(value: Any) -> Decimal | None:
    """يحول قيمة لـ Decimal — يفهم الصيغة الإيطالية ("8,50") ويشيل العملة.

    يرجع None لو فشل. القيم الرقمية اللي جاية من Excel (int/float) بتتحول
    عن طريق str() من غير تخمين للفواصل.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and (pd.isna(value) or value in (float("inf"), float("-inf"))):
            return None
        return Decimal(str(value))

    cleaned = clean_value(value)
    if cleaned is None:
        return None
    digits = re.sub(r"[^\d.,\-]", "", str(cleaned))
    if not re.fullmatch(r"-?[\d.,]*\d[\d.,]*", digits):
        return None
    try:
        result = Decimal(_canonical_decimal_string(digits))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


_TRUTHY = {"true", "yes", "y", "1", "si", "sì", "vero", "disponibile",
           "in_stock", "in stock", "available", "نعم", "متوفر", "متاح"}
_FALSY = {"false", "no", "n", "0", "falso", "non_disponibile",
          "non disponibile", "out_of_stock", "out of stock", "unavailable",
          "لا", "غير متوفر", "غير متاح", "نفذ"}


def parse_bool(value: Any) -> bool | None:
    """يحول قيمة لـ True/False — يقبل صيغ إنجليزية وإيطالية وعربية. None لو فشل."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return bool(value)
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    s = str(cleaned).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return None


# -----------------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------------
def _coalesce_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """لو عمودين اتطابقوا على نفس الاسم (مثلاً price + prezzo)، ناخد أول قيمة non-null"""
    if df.columns.is_unique:
        return df

    out = pd.DataFrame(index=df.index)
    for name in df.columns.unique():
        sub = df.loc[:, df.columns == name]
        if sub.shape[1] == 1:
            out[name] = sub.iloc[:, 0]
        else:
            out[name] = sub.bfill(axis=1).iloc[:, 0]
    return out


def normalize_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """
    يطبع جدول المنيو: يبدّل أسماء الأعمدة + ينضف + يحول الأرقام والـ booleans.

    Returns:
        (normalized_df, unmapped_columns)
    """
    mapping, unmapped = map_columns(list(df.columns))
    df = df.rename(columns=mapping).copy()
    df = _coalesce_duplicate_columns(df)

    for col in df.columns:
        if col in _NUMERIC_FIELDS:
            df[col] = df[col].apply(parse_number)
        elif col in _BOOLEAN_FIELDS:
            df[col] = df[col].apply(parse_bool)
        else:
            df[col] = df[col].apply(clean_value)

    return df, unmapped

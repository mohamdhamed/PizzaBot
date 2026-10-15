# normalizer.py
# توحيد أعمدة ملف المنيو + تنظيف القيم (إيطالي / إنجليزي / عربي)
# منقول من Madaar (rag/normalizer.py) — الجزء الخاص بالبيتزا بس.

import re
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


def parse_number(value: Any) -> float | None:
    """يحول قيمة لرقم — يشيل الفواصل والعملة. يرجع None لو فشل."""
    cleaned = clean_value(value)
    if cleaned is None:
        return None
    s = str(cleaned)
    digits = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if not digits or digits in {".", "-", "-."}:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


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

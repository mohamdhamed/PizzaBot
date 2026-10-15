# excel_safety.py
# الطبقة التالتة — فحص أمان مخصص لـ XLSX
# يفحص: zip bombs, macros, external links, formula injection

import re
import zipfile
from pathlib import Path

# Zip bomb defense
MAX_DECOMPRESSION_RATIO = 100      # uncompressed_size / compressed_size
MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024   # 500 MB

# Formula prefixes خطرة (CSV/Excel injection)
DANGEROUS_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t=", "\r=")
DANGEROUS_FORMULAS = ("WEBSERVICE", "FILTERXML", "DDE", "HYPERLINK", "EXEC", "CALL")


class ExcelSafetyError(Exception):
    """يُطلق لما XLSX يفشل فحص الأمان"""


def check_xlsx(path: Path) -> None:
    """فحص أمان شامل لملف XLSX. يطلق ExcelSafetyError لو فيه مشكلة."""
    try:
        with zipfile.ZipFile(path) as zf:
            _check_zip_bomb(zf)
            _check_no_macros(zf)
            _check_no_external_links(zf)
    except zipfile.BadZipFile:
        raise ExcelSafetyError("الملف ليس XLSX صالح (ليس ZIP)")


def _check_zip_bomb(zf: zipfile.ZipFile) -> None:
    """يحسب نسبة الضغط ومجموع الحجم بعد فك الضغط"""
    total_uncompressed = 0
    for info in zf.infolist():
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED:
            raise ExcelSafetyError(
                f"الحجم بعد فك الضغط {total_uncompressed:,} bytes "
                f"يتجاوز الحد {MAX_TOTAL_UNCOMPRESSED:,} (zip bomb محتمل)"
            )

        if info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_DECOMPRESSION_RATIO:
                raise ExcelSafetyError(
                    f"نسبة ضغط مشبوهة في '{info.filename}': "
                    f"{ratio:.1f}x (zip bomb محتمل)"
                )


def _check_no_macros(zf: zipfile.ZipFile) -> None:
    """يرفض أي ملف فيه VBA macros"""
    macro_indicators = ("vbaProject.bin", "macros/", "xl/macroSheets/")
    for name in zf.namelist():
        if any(ind in name for ind in macro_indicators):
            raise ExcelSafetyError(f"الملف يحتوي على macros: '{name}' (مرفوض)")


def _check_no_external_links(zf: zipfile.ZipFile) -> None:
    """يرفض الملفات اللي فيها روابط خارجية أو formulas خطرة"""
    # External links files
    for name in zf.namelist():
        if name.startswith("xl/externalLinks/"):
            raise ExcelSafetyError(
                f"الملف يحتوي على روابط خارجية: '{name}' (مرفوض لأمان البيانات)"
            )

    # فحص أول 10 sheets فقط (للأداء)
    sheet_files = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
    for sheet_name in sheet_files[:10]:
        try:
            content = zf.read(sheet_name).decode("utf-8", errors="ignore")
        except Exception:
            continue

        # بنفحص جوا عناصر <f> بس — النصوص العادية في الخلايا (زي
        # "addensante") مش formulas ومفروض ما تترفضش.
        for formula in _FORMULA_RE.findall(content):
            danger = _dangerous_in_formula(formula)
            if danger:
                raise ExcelSafetyError(
                    f"formula خطرة '{danger}' موجودة في {sheet_name}"
                )


_FORMULA_RE = re.compile(r"<f\b[^>]*>(.*?)</f>", re.S)
_FUNCTION_CALL_RE = re.compile(
    r"\b(" + "|".join(DANGEROUS_FORMULAS) + r")\s*\(", re.I
)
# DDE: =cmd|'/c calc'!A1 — اسم برنامج وبعده pipe
_DDE_RE = re.compile(r"[A-Za-z0-9_.]+\s*\|")


def _dangerous_in_formula(formula: str) -> str | None:
    """يرجع اسم الخطر لو الـ formula فيها function خطرة أو DDE، وإلا None"""
    m = _FUNCTION_CALL_RE.search(formula)
    if m:
        return m.group(1).upper()
    if _DDE_RE.search(formula):
        return "DDE"
    return None

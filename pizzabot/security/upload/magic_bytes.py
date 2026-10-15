# magic_bytes.py
# الطبقة التانية — التحقق من النوع الحقيقي عبر أول bytes في الملف
# (لا نصدق الامتداد — نتحقق من المحتوى)

from pathlib import Path

# توقيعات أول bytes للملفات المسموحة
_SIGNATURES = {
    ".xlsx": [b"PK\x03\x04"],   # XLSX = ZIP
    ".pdf":  [b"%PDF-"],
}


class MagicBytesError(Exception):
    """يُطلق لما الـ magic bytes لا تطابق الامتداد"""


def verify_magic_bytes(path: Path, expected_ext: str) -> None:
    """يفتح الملف ويقرأ أول 8 bytes ويقارنهم بالتوقيعات المتوقعة"""
    expected = _SIGNATURES.get(expected_ext)
    if not expected:
        raise MagicBytesError(f"امتداد غير مدعوم: {expected_ext}")

    with path.open("rb") as f:
        head = f.read(8)

    if not any(head.startswith(sig) for sig in expected):
        raise MagicBytesError(
            f"محتوى الملف لا يطابق امتداد {expected_ext} "
            f"(أول bytes: {head[:8]!r})"
        )

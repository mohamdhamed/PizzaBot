# policy.py
# الطبقة الأولى — قواعد الإدخال الأساسية (whitelist)

import re
from pathlib import Path

ALLOWED_EXTENSIONS: set[str] = {".xlsx", ".pdf"}

MAX_FILE_SIZE: int = 50 * 1024 * 1024  # 50 MB

# عربي + إنجليزي + أرقام + . - _ مسافات (حد أقصى 200 حرف)
_FILENAME_RE = re.compile(r"^[\w؀-ۿ\-\.\s]{1,200}$")


class PolicyError(Exception):
    """يُطلق لما الملف يكسر سياسة الإدخال"""


def check_extension(filename: str) -> str:
    """يتأكد إن الامتداد ضمن الـ whitelist ويرجعه لو OK"""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise PolicyError(
            f"الامتداد '{ext}' غير مسموح. المسموح: {sorted(ALLOWED_EXTENSIONS)}"
        )
    return ext


def check_filename(filename: str) -> None:
    """يمنع أسماء ملفات خطرة (path traversal, unicode tricks, إلخ)"""
    name = Path(filename).name  # يشيل أي مسار

    if name != filename:
        raise PolicyError("اسم الملف لا يجوز أن يحتوي على مسار")

    if not _FILENAME_RE.match(name):
        raise PolicyError(
            "اسم الملف يحتوي على رموز غير مسموحة. استخدم حروف/أرقام/مسافات فقط"
        )

    if "\x00" in name or any(0 <= ord(c) < 32 for c in name):
        raise PolicyError("اسم الملف يحتوي على control characters")


def check_size(size_bytes: int) -> None:
    """يتأكد إن الملف ما تجاوزش الحد الأقصى"""
    if size_bytes <= 0:
        raise PolicyError("الملف فاضي")
    if size_bytes > MAX_FILE_SIZE:
        raise PolicyError(
            f"حجم الملف {size_bytes:,} bytes يتجاوز الحد {MAX_FILE_SIZE:,} bytes"
        )


def check_path_safe(path: Path, allowed_root: Path) -> Path:
    """يمنع path traversal — يتأكد إن المسار جوا الـ allowed_root"""
    resolved = path.resolve()
    allowed = allowed_root.resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError:
        raise PolicyError(
            f"المسار خارج الفولدر المسموح: {resolved} ليس داخل {allowed}"
        )
    return resolved

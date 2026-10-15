# validator.py
# منسق طبقات الأمان — يشغل كل الفحوصات بالترتيب
# الـ entry point الأساسي: validate_file()

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pizzabot.security.upload import policy, magic_bytes, excel_safety, pdf_safety


@dataclass
class ValidationResult:
    """نتيجة فحص الملف"""
    accepted: bool
    filename: str
    size: int
    sha256: str
    extension: str = ""
    reason: str = ""           # سبب الرفض لو موجود
    checks_passed: list[str] = field(default_factory=list)
    quarantine_path: Path | None = None

    def to_dict(self) -> dict:
        d = {
            "accepted": self.accepted,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "extension": self.extension,
            "reason": self.reason,
            "checks_passed": self.checks_passed,
        }
        if self.quarantine_path:
            d["quarantine_path"] = str(self.quarantine_path)
        return d


def _sha256(path: Path) -> str:
    """يحسب hash للملف لتمييز التكرار"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _quarantine(path: Path, quarantine_dir: Path, reason: str) -> Path:
    """ينقل الملف لفولدر الحجر مع timestamp ويسيب سبب الرفض"""
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = quarantine_dir / f"{ts}__{path.name}"
    shutil.move(str(path), str(target))
    (quarantine_dir / f"{target.name}.reason.txt").write_text(reason, encoding="utf-8")
    return target


def _log(log_file: Path, result: ValidationResult) -> None:
    """يكتب نتيجة الفحص في ملف الـ audit"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        **result.to_dict(),
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def validate_file(
    path: Path,
    *,
    allowed_root: Path | None = None,
    quarantine_dir: Path | None = None,
    log_file: Path | None = None,
    move_on_fail: bool = True,
) -> ValidationResult:
    """
    يشغل كل طبقات الأمان على الملف ويرجع النتيجة.

    لو فشل وكانت move_on_fail=True ينقل الملف للحجر تلقائياً.

    Args:
        path: مسار الملف اللي بنفحصه
        allowed_root: لو معطى، نتأكد إن المسار جواه (path traversal defense)
        quarantine_dir: مكان الحجر — افتراضياً data/quarantine
        log_file: ملف audit — افتراضياً data/ingestion.log
        move_on_fail: لو False، نسيب الملف مكانه ونرجع النتيجة بس
    """
    filename = path.name
    result = ValidationResult(
        accepted=False, filename=filename, size=0, sha256="",
    )

    try:
        # طبقة 1: filename + path safety
        policy.check_filename(filename)
        if allowed_root is not None:
            policy.check_path_safe(path, allowed_root)
        result.checks_passed.append("filename_safe")

        # طبقة 2: existence + size
        if not path.exists() or not path.is_file():
            raise policy.PolicyError("الملف غير موجود")
        size = path.stat().st_size
        result.size = size
        policy.check_size(size)
        result.checks_passed.append("size_ok")

        # طبقة 3: extension whitelist
        ext = policy.check_extension(filename)
        result.extension = ext
        result.checks_passed.append("extension_allowed")

        # طبقة 4: hash
        result.sha256 = _sha256(path)
        result.checks_passed.append("hashed")

        # طبقة 5: magic bytes
        magic_bytes.verify_magic_bytes(path, ext)
        result.checks_passed.append("magic_bytes_match")

        # طبقة 6: type-specific safety
        if ext == ".xlsx":
            excel_safety.check_xlsx(path)
            result.checks_passed.append("xlsx_safety_ok")
        elif ext == ".pdf":
            pdf_safety.check_pdf(path)
            result.checks_passed.append("pdf_safety_ok")

        result.accepted = True

    except (
        policy.PolicyError,
        magic_bytes.MagicBytesError,
        excel_safety.ExcelSafetyError,
        pdf_safety.PdfSafetyError,
    ) as e:
        result.accepted = False
        result.reason = str(e)
        if move_on_fail and path.exists():
            qd = quarantine_dir or _default_quarantine_dir()
            try:
                result.quarantine_path = _quarantine(path, qd, result.reason)
            except Exception as quarantine_err:
                result.reason += f" | فشل النقل للحجر: {quarantine_err}"

    finally:
        try:
            _log(log_file or _default_log_file(), result)
        except Exception:
            pass  # log failures shouldn't break the pipeline

    return result


def _default_quarantine_dir() -> Path:
    from pizzabot.config import ROOT
    return ROOT / "data" / "quarantine"


def _default_log_file() -> Path:
    from pizzabot.config import ROOT
    return ROOT / "data" / "ingestion.log"

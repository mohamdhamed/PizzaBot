# pdf_safety.py
# الطبقة التالتة — فحص أمان PDF
# يفحص: JavaScript, embedded files, launch actions, suspicious URIs

from pathlib import Path
from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Actions خطرة في الـ PDF
DANGEROUS_ACTIONS = ("/JS", "/JavaScript", "/Launch", "/SubmitForm", "/ImportData")


class PdfSafetyError(Exception):
    """يُطلق لما PDF يفشل فحص الأمان"""


def check_pdf(path: Path) -> None:
    """فحص أمان شامل لملف PDF. يطلق PdfSafetyError لو فيه مشكلة."""
    try:
        reader = PdfReader(path, strict=False)
    except PdfReadError as e:
        raise PdfSafetyError(f"PDF تالف أو غير صالح: {e}")

    if reader.is_encrypted:
        raise PdfSafetyError("الـ PDF مشفر — مرفوض")

    _check_no_javascript(reader)
    _check_no_embedded_files(reader)
    _check_no_dangerous_actions(reader)


def _check_no_javascript(reader: PdfReader) -> None:
    """يرفض أي PDF فيه JavaScript"""
    try:
        catalog = reader.trailer["/Root"]
    except Exception:
        return

    if "/Names" in catalog:
        names = catalog["/Names"]
        if hasattr(names, "get_object"):
            names = names.get_object()
        if "/JavaScript" in names:
            raise PdfSafetyError("الـ PDF يحتوي على JavaScript (مرفوض)")

    if "/OpenAction" in catalog:
        action = catalog["/OpenAction"]
        if hasattr(action, "get_object"):
            action = action.get_object()
        if isinstance(action, dict):
            if action.get("/S") in ("/JavaScript", "/JS"):
                raise PdfSafetyError("الـ PDF فيه OpenAction JavaScript (مرفوض)")


def _check_no_embedded_files(reader: PdfReader) -> None:
    """يرفض أي PDF فيه ملفات مدمجة"""
    try:
        catalog = reader.trailer["/Root"]
        if "/Names" in catalog:
            names = catalog["/Names"]
            if hasattr(names, "get_object"):
                names = names.get_object()
            if "/EmbeddedFiles" in names:
                raise PdfSafetyError("الـ PDF يحتوي على ملفات مدمجة (مرفوض)")
    except KeyError:
        pass


def _check_no_dangerous_actions(reader: PdfReader) -> None:
    """يفحص الصفحات يدور على actions خطرة"""
    for page_num, page in enumerate(reader.pages[:50], 1):  # حد أقصى 50 صفحة
        if "/AA" in page:  # Additional Actions
            raise PdfSafetyError(
                f"صفحة {page_num} فيها Additional Actions (مرفوض)"
            )
        if "/Annots" in page:
            try:
                annots = page["/Annots"]
                if hasattr(annots, "get_object"):
                    annots = annots.get_object()
                for annot_ref in annots:
                    annot = annot_ref.get_object() if hasattr(annot_ref, "get_object") else annot_ref
                    if not isinstance(annot, dict):
                        continue
                    action = annot.get("/A")
                    if action and hasattr(action, "get_object"):
                        action = action.get_object()
                    if isinstance(action, dict):
                        action_type = action.get("/S")
                        if action_type in DANGEROUS_ACTIONS:
                            raise PdfSafetyError(
                                f"صفحة {page_num} فيها action خطر: {action_type}"
                            )
            except Exception:
                continue

# tests/test_security_excel.py — فحص أمان XLSX

import zipfile
from pathlib import Path
import pytest
import pandas as pd
from pizzabot.security.upload import excel_safety


def test_clean_xlsx_passes(tmp_path: Path):
    path = tmp_path / "clean.xlsx"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_excel(path, index=False)
    excel_safety.check_xlsx(path)


def test_not_a_zip_fails(tmp_path: Path):
    path = tmp_path / "fake.xlsx"
    path.write_bytes(b"not a zip")
    with pytest.raises(excel_safety.ExcelSafetyError):
        excel_safety.check_xlsx(path)


def test_xlsx_with_macros_rejected(tmp_path: Path):
    """ملف فيه vbaProject.bin لازم يترفض"""
    base = tmp_path / "with_macros.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(base, index=False)

    # نضيف vbaProject.bin للـ ZIP يدوياً
    with zipfile.ZipFile(base, "a") as zf:
        zf.writestr("xl/vbaProject.bin", b"fake macro binary")

    with pytest.raises(excel_safety.ExcelSafetyError, match="macros"):
        excel_safety.check_xlsx(base)


def test_xlsx_with_external_links_rejected(tmp_path: Path):
    base = tmp_path / "with_links.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(base, index=False)
    with zipfile.ZipFile(base, "a") as zf:
        zf.writestr("xl/externalLinks/externalLink1.xml", "<link/>")
    with pytest.raises(excel_safety.ExcelSafetyError, match="روابط خارجية"):
        excel_safety.check_xlsx(base)


def test_xlsx_with_dangerous_formula_rejected(tmp_path: Path):
    base = tmp_path / "with_formula.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(base, index=False)

    # نحقن WEBSERVICE في sheet1
    with zipfile.ZipFile(base, "r") as zf:
        sheet_content = zf.read("xl/worksheets/sheet1.xml").decode()

    sheet_content = sheet_content.replace(
        "<sheetData>",
        '<sheetData><row r="99"><c r="A99"><f>WEBSERVICE("http://evil.com")</f></c></row>'
    )

    # نعيد كتابة الـ ZIP
    new_path = tmp_path / "evil.xlsx"
    with zipfile.ZipFile(base, "r") as old, zipfile.ZipFile(new_path, "w") as new:
        for item in old.infolist():
            data = old.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = sheet_content.encode()
            new.writestr(item, data)

    with pytest.raises(excel_safety.ExcelSafetyError, match="WEBSERVICE"):
        excel_safety.check_xlsx(new_path)


def test_zip_bomb_detected(tmp_path: Path):
    """ملف بنسبة ضغط عالية جداً لازم يترفض"""
    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 10MB من الأصفار يضغطوا لكام KB → ratio عالي
        zf.writestr("payload.bin", b"\x00" * (10 * 1024 * 1024))
    with pytest.raises(excel_safety.ExcelSafetyError):
        excel_safety.check_xlsx(bomb)

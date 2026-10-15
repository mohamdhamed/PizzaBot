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


def _with_cell_xml(tmp_path: Path, cell_xml: str) -> Path:
    """يعمل xlsx ويحقن خلية XML جاهزة في sheet1"""
    base = tmp_path / "base.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(base, index=False)
    out = tmp_path / "injected.xlsx"
    with zipfile.ZipFile(base, "r") as old, zipfile.ZipFile(out, "w") as new:
        for item in old.infolist():
            data = old.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.decode().replace(
                    "<sheetData>",
                    f'<sheetData><row r="99">{cell_xml}</row>',
                ).encode()
            new.writestr(item, data)
    return out


@pytest.mark.parametrize("text", [
    "mascarpone, addensante",   # فيها DDE
    "Pizza CALLzone speciale",  # فيها CALL
    "Hyperlink, exec, webservice",
])
def test_plain_text_with_formula_names_passes(tmp_path: Path, text: str):
    """كلمات عادية في المنيو مش formulas — ما تترفضش"""
    path = tmp_path / "menu.xlsx"
    pd.DataFrame([{"nome": "Tiramisù", "ingredienti": text}]).to_excel(path, index=False)
    excel_safety.check_xlsx(path)


def test_harmless_formula_passes(tmp_path: Path):
    path = _with_cell_xml(tmp_path, '<c r="A99"><f>SUM(B1:B3)*1.1</f></c>')
    excel_safety.check_xlsx(path)


@pytest.mark.parametrize("formula,label", [
    ("HYPERLINK(&quot;http://evil&quot;)", "HYPERLINK"),
    ("call (&quot;x&quot;)", "CALL"),
    ("cmd|'/c calc'!A1", "DDE"),
])
def test_dangerous_formulas_rejected(tmp_path: Path, formula: str, label: str):
    path = _with_cell_xml(tmp_path, f'<c r="A99"><f>{formula}</f></c>')
    with pytest.raises(excel_safety.ExcelSafetyError, match=label):
        excel_safety.check_xlsx(path)

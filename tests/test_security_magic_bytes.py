# tests/test_security_magic_bytes.py — التحقق من النوع الحقيقي

from pathlib import Path
import pytest
import pandas as pd
from pizzabot.security.upload import magic_bytes


def test_real_xlsx_passes(tmp_path: Path):
    path = tmp_path / "data.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(path, index=False)
    magic_bytes.verify_magic_bytes(path, ".xlsx")


def test_text_with_xlsx_extension_fails(tmp_path: Path):
    """ملف نصي اتسمى .xlsx لازم يترفض"""
    fake = tmp_path / "fake.xlsx"
    fake.write_text("not an excel file")
    with pytest.raises(magic_bytes.MagicBytesError):
        magic_bytes.verify_magic_bytes(fake, ".xlsx")


def test_real_pdf_passes(tmp_path: Path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n%fake but valid header\n")
    magic_bytes.verify_magic_bytes(path, ".pdf")


def test_text_with_pdf_extension_fails(tmp_path: Path):
    fake = tmp_path / "fake.pdf"
    fake.write_text("hello not a pdf")
    with pytest.raises(magic_bytes.MagicBytesError):
        magic_bytes.verify_magic_bytes(fake, ".pdf")


def test_unsupported_extension_raises(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x00\x01")
    with pytest.raises(magic_bytes.MagicBytesError):
        magic_bytes.verify_magic_bytes(f, ".bin")

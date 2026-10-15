# tests/test_security_validator.py — منسق طبقات الأمان

from pathlib import Path
import pandas as pd
from pizzabot.security.upload.validator import validate_file


def test_clean_excel_accepted(sample_excel: Path, tmp_quarantine_dir: Path, tmp_path: Path):
    log = tmp_path / "ingestion.log"
    result = validate_file(
        sample_excel,
        quarantine_dir=tmp_quarantine_dir,
        log_file=log,
    )
    assert result.accepted is True
    assert result.extension == ".xlsx"
    assert len(result.sha256) == 64
    assert "magic_bytes_match" in result.checks_passed
    assert "xlsx_safety_ok" in result.checks_passed
    assert log.exists()


def test_bad_extension_rejected_and_quarantined(tmp_path: Path, tmp_quarantine_dir: Path):
    f = tmp_path / "evil.exe"
    f.write_bytes(b"MZ\x90")  # exe header
    log = tmp_path / "log.jsonl"

    result = validate_file(
        f, quarantine_dir=tmp_quarantine_dir, log_file=log,
    )
    assert result.accepted is False
    assert "غير مسموح" in result.reason
    assert result.quarantine_path is not None
    assert result.quarantine_path.exists()
    assert not f.exists(), "الملف الأصلي لازم يتنقل"


def test_fake_xlsx_rejected(tmp_path: Path, tmp_quarantine_dir: Path):
    fake = tmp_path / "fake.xlsx"
    fake.write_text("not really an excel file")
    result = validate_file(
        fake,
        quarantine_dir=tmp_quarantine_dir,
        log_file=tmp_path / "log.jsonl",
    )
    assert result.accepted is False
    assert result.quarantine_path is not None


def test_no_move_on_fail(tmp_path: Path, tmp_quarantine_dir: Path):
    f = tmp_path / "bad.exe"
    f.write_bytes(b"x")
    result = validate_file(
        f,
        quarantine_dir=tmp_quarantine_dir,
        log_file=tmp_path / "log.jsonl",
        move_on_fail=False,
    )
    assert result.accepted is False
    assert f.exists(), "بدون move_on_fail الملف يفضل مكانه"


def test_path_traversal_blocked(tmp_path: Path):
    """ملف برة الـ allowed_root لازم يترفض"""
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(outside, index=False)

    result = validate_file(
        outside,
        allowed_root=inside,
        quarantine_dir=tmp_path / "q",
        log_file=tmp_path / "log.jsonl",
    )
    assert result.accepted is False

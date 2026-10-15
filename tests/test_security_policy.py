# tests/test_security_policy.py — قواعد الإدخال

from pathlib import Path
import pytest
from pizzabot.security.upload import policy


class TestExtension:
    def test_allows_xlsx(self):
        assert policy.check_extension("data.xlsx") == ".xlsx"

    def test_allows_pdf(self):
        assert policy.check_extension("brochure.PDF") == ".pdf"

    def test_rejects_xlsm(self):
        with pytest.raises(policy.PolicyError):
            policy.check_extension("evil.xlsm")

    def test_rejects_exe(self):
        with pytest.raises(policy.PolicyError):
            policy.check_extension("evil.exe")

    def test_rejects_no_extension(self):
        with pytest.raises(policy.PolicyError):
            policy.check_extension("data")


class TestFilename:
    def test_allows_arabic(self):
        policy.check_filename("بيانات.xlsx")

    def test_allows_english(self):
        policy.check_filename("data.xlsx")

    def test_allows_spaces(self):
        policy.check_filename("project data.xlsx")

    def test_rejects_path_in_name(self):
        with pytest.raises(policy.PolicyError):
            policy.check_filename("../etc/passwd")

    def test_rejects_null_byte(self):
        with pytest.raises(policy.PolicyError):
            policy.check_filename("evil\x00.xlsx")

    def test_rejects_too_long(self):
        with pytest.raises(policy.PolicyError):
            policy.check_filename("a" * 250 + ".xlsx")


class TestSize:
    def test_allows_normal(self):
        policy.check_size(1024)

    def test_rejects_empty(self):
        with pytest.raises(policy.PolicyError):
            policy.check_size(0)

    def test_rejects_too_large(self):
        with pytest.raises(policy.PolicyError):
            policy.check_size(policy.MAX_FILE_SIZE + 1)


class TestPathSafe:
    def test_allows_inside_root(self, tmp_path: Path):
        f = tmp_path / "data.xlsx"
        f.write_text("x")
        result = policy.check_path_safe(f, tmp_path)
        assert result == f.resolve()

    def test_rejects_outside_root(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        inside_root = tmp_path / "inside"
        inside_root.mkdir()
        outside_file = outside / "evil.xlsx"
        outside_file.write_text("x")
        with pytest.raises(policy.PolicyError):
            policy.check_path_safe(outside_file, inside_root)

# conftest.py
# fixtures مشتركة لكل tests

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "menus"
    d.mkdir()
    return d


@pytest.fixture
def tmp_quarantine_dir(tmp_path: Path) -> Path:
    d = tmp_path / "quarantine"
    d.mkdir()
    return d


@pytest.fixture
def sample_excel(tmp_data_dir: Path) -> Path:
    """ملف منيو بسيط بصفين للاختبار"""
    df = pd.DataFrame([
        {"nome": "Margherita", "prezzo": "8.50", "categoria": "Pizza Classica",
         "disponibile": "si", "ingredienti": "pomodoro, mozzarella, basilico"},
        {"nome": "Diavola", "prezzo": "10.00", "categoria": "Pizza Classica",
         "disponibile": "si", "ingredienti": "pomodoro, mozzarella, salame piccante"},
    ])
    path = tmp_data_dir / "sample.xlsx"
    df.to_excel(path, index=False)
    return path

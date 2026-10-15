# config.py
# المسارات الأساسية للمشروع

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
QUARANTINE_DIR = DATA_DIR / "quarantine"

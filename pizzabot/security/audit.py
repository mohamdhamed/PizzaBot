# audit.py
# سجل كل الـ queries في JSONL — للمراجعة وكشف الاستخدام المشبوه

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


def _default_log_file() -> Path:
    from pizzabot.config import ROOT
    return ROOT / "data" / "queries.jsonl"


def _hash_text(text: str) -> str:
    """fingerprint قصير للسؤال — لكشف التكرار من غير ما نخزنه كامل"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def log_query(
    *,
    endpoint: Literal["ask", "chat", "upload"],
    tier: str,
    ip: str,
    session_id: str | None,
    question: str,
    decision: Literal["allowed", "blocked"],
    reason: str = "",
    sources_count: int = 0,
    answer_length: int = 0,
    extra: dict[str, Any] | None = None,
    log_file: Path | None = None,
) -> None:
    """يسجل كل عملية في الـ audit log (JSONL — سطر لكل request)"""
    target = log_file or _default_log_file()
    target.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "endpoint": endpoint,
        "tier": tier,
        "ip": ip,
        "session_id": session_id,
        "decision": decision,
        "reason": reason,
        "question_hash": _hash_text(question) if question else "",
        "question_length": len(question or ""),
        "question_preview": (question or "")[:120],
        "sources_count": sources_count,
        "answer_length": answer_length,
    }
    if extra:
        entry["extra"] = extra

    try:
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging shouldn't break the request

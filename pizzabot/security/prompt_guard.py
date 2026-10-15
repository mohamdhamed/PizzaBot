# prompt_guard.py
# منع prompt injection و jailbreak و data exfiltration patterns

import re

# Patterns صريحة للهجوم — يطابق RegEx بحساسية متوسطة
_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Jailbreak / system prompt override
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)", re.I),
     "ignore previous instructions"),
    (re.compile(r"(disregard|forget)\s+(all\s+)?(previous|prior)", re.I),
     "disregard previous"),
    (re.compile(r"(تجاهل|انسى)\s+(كل\s+)?(التعليمات|الأوامر|البرومبت)", re.I),
     "تجاهل التعليمات"),

    # Role override
    (re.compile(r"you\s+are\s+now\s+(a\s+)?(?!aqar)", re.I), "role override"),
    (re.compile(r"act\s+as\s+(a\s+|an\s+)?(?!real\s*estate|عقار)", re.I), "act as"),
    (re.compile(r"pretend\s+(you|to\s+be)", re.I), "pretend"),

    # System prompt extraction
    (re.compile(r"(show|print|reveal|display|repeat)\s+(me\s+)?(your\s+|the\s+)?(system|initial)\s+prompt", re.I),
     "system prompt extraction"),
    (re.compile(r"what\s+(are|were)\s+your\s+(initial\s+|original\s+)?instructions", re.I),
     "instruction extraction"),

    # Bulk data exfiltration
    (re.compile(r"(list|show|give|export|dump)\s+(me\s+)?(all|every|the\s+entire|complete)\s+(data|database|records|properties|units|customers|agents)", re.I),
     "bulk data request"),
    (re.compile(r"(اعرض|اعطني|اطبع|صدر)\s+(كل|جميع)\s+(البيانات|قاعدة\s+البيانات|السجلات|العقارات|الوحدات|العملاء)", re.I),
     "طلب بيانات شامل"),

    # Prompt delimiters injection
    (re.compile(r"```\s*system\s*\n", re.I), "system block injection"),
    (re.compile(r"<\|im_start\|>\s*system", re.I), "chat template injection"),
]


class PromptInjectionError(Exception):
    """يُطلق عند اكتشاف نمط prompt injection"""


def scan_prompt(text: str, *, max_length: int) -> None:
    """
    يفحص نص العميل ويرفع PromptInjectionError لو فيه نمط مشبوه.

    Args:
        text: السؤال أو الرسالة
        max_length: الحد الأقصى للطول حسب الـ tier
    """
    if not text or not text.strip():
        raise PromptInjectionError("السؤال فاضي")

    if len(text) > max_length:
        raise PromptInjectionError(
            f"السؤال أطول من الحد المسموح ({len(text)} > {max_length})"
        )

    # null bytes / control chars
    if "\x00" in text or any(0 <= ord(c) < 9 or 11 <= ord(c) < 32 for c in text):
        raise PromptInjectionError("السؤال يحتوي على control characters")

    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise PromptInjectionError(f"نمط مشبوه: {label}")


def is_safe(text: str, *, max_length: int) -> bool:
    """نسخة بدون استثناء — True/False"""
    try:
        scan_prompt(text, max_length=max_length)
        return True
    except PromptInjectionError:
        return False

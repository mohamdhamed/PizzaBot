# PizzaBot

Order-management bot for small Italian pizzerias (WhatsApp / Telegram).
Guided button flow — no free chat, no LLM.
Operator: PULSAR SERVICE S.A.S. (Monza, Italy).

بوت طلبات للبيتزاريات الإيطالية الصغيرة. مشروع مستقل — اتفصل عن Madaar/AqarBrain يوم 2026-10-15.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
```

## Layout

```
pizzabot/
├── config.py               ROOT / DATA_DIR / QUARANTINE_DIR
├── order/schema.py         Order, CartItem, OrderStatus (Pydantic, Decimal EUR, IVA scorporo)
├── menu/normalizer.py      menu Excel columns (it/en/ar) → dish_name, price_eur, category, available, ingredients
└── security/
    ├── upload/             5-layer file validation (policy, magic bytes, xlsx/pdf safety, quarantine)
    ├── tiers.py            access tiers + limits        ⚠ see CONTEXT.md
    ├── rate_limiter.py     sliding window per (tier, IP)
    ├── prompt_guard.py     injection patterns    ⚠ unused with guided flow, see CONTEXT.md
    ├── pii_masker.py       phone + email masking
    └── audit.py            JSONL request log → data/queries.jsonl
tests/                      one test file per module
```

See [CONTEXT.md](CONTEXT.md) for design decisions, known gaps, and the next sprint.

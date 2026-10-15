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

Try the bot locally (no Telegram needed):

```bash
.venv/bin/python -m pizzabot.simulator
```

Then open http://127.0.0.1:8765.

## Layout

```
pizzabot/
├── config.py               ROOT / DATA_DIR / QUARANTINE_DIR
├── flow/
│   ├── menu.py             menu, option groups, allergens, restaurant settings; price rule
│   ├── slots.py            opening hours + pickup/delivery slots
│   ├── machine.py          guided-flow state machine: handle(conversation, event, ctx)
│   └── demo.py             demo restaurant (synthetic menu)
├── simulator.py            local web chat to try the flow (stdlib only)
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

# CONTEXT.md — PizzaBot

**Created:** 2026-10-15 · split out of `/mnt/windows/AqarBrain` (Madaar).

ملف السياق للجلسات الجاية. اقرأه الأول.

---

## 1. Origin

PizzaBot started as `apps/pizza/` inside Madaar (a RAG knowledge-assist project
for Egyptian real estate). On 2026-10-15 it was split into this standalone
project because the two products differ in data model, market, language,
compliance, and pace. Nothing here imports from Madaar.

### What was brought over (copied, then re-namespaced)

| Here | From Madaar | Notes |
|---|---|---|
| `pizzabot/order/schema.py` | `apps/pizza/order/schema.py` | unchanged |
| `pizzabot/menu/normalizer.py` | pizza parts of `rag/normalizer.py` | real-estate fields dropped; `price` / `السعر` now map to `price_eur` |
| `pizzabot/security/upload/` | `rag/security/` | unchanged logic |
| `pizzabot/security/{tiers,rate_limiter,prompt_guard,pii_masker,audit}.py` | `api/security/` | unchanged logic; `audit` default path now `pizzabot.config.ROOT` |
| tests | matching Madaar tests | `sample_excel` fixture is now a menu |

Deliberately **not** brought over: RAG (`rag/` retriever, Chroma, embeddings,
Ollama), `chat/` (real-estate lead scoring), `shared_eval/` (retrieval-specific).

---

## 2. Design decisions (locked)

1. **No RAG for the menu.** A pizzeria menu (20–60 items) fits in the LLM
   context as structured data. No vector store, no embeddings.
2. **Money never comes from model text.** Prices, availability, cart totals,
   and VAT (22%) are computed from the database. The LLM only parses intent
   and calls tools (`add_to_cart`, `remove_item`, `checkout`, …).
   Money is `Decimal` (2 places, half-up) end to end. Menu prices are
   IVA-inclusive; `Order` extracts IVA from the gross total (`vat_eur`,
   `net_eur`, `net + vat == total`). `vat_rate` defaults to 22% but is a
   per-order field — **verify the rate**: Italian food service/takeaway is
   usually 10%, 22% is the standard rate.
3. **Multi-tenant from day one.** Every menu row, cart, order, and log line
   carries a `restaurant_id`. No shared global state across restaurants.
4. **Real authentication.** Restaurant operators authenticate (API key or
   token per restaurant). Customers are identified by channel + phone.

---

## 3. Known gaps in the copied code (fix before production)

| Area | Issue |
|---|---|
| `security/tiers.py` + `rate_limiter.py` | Tier comes from the client-sent `X-Tier` header → anyone can claim `agent`. Tiers are real-estate names (`anonymous/lead/agent`). Replace with authenticated roles (`customer / restaurant / admin`). |
| `security/prompt_guard.py` | Patterns are Arabic + English only — add Italian. |
| `security/pii_masker.py` | Phone regex was tuned for Egyptian/Saudi numbers — verify Italian `+39` formats. |
| `security/upload/*` | User-facing error messages are in Arabic — operators are Italian. |
| GDPR | Customer phone numbers and chat history are PII. Retention policy and data-processing location must be decided (affects LLM choice). |

---

## 4. Sprint 1 (next)

| Deliverable | Notes |
|---|---|
| Menu storage (PostgreSQL) | `restaurants`, `menu_items` with `restaurant_id`; import via `security/upload` → `menu/normalizer` |
| Cart | Redis, TTL 2h, keyed by `(restaurant_id, customer)`; prices read from DB |
| Checkout | persist `Order` to PostgreSQL; IVA 22% |
| Auth | per-restaurant credentials, replaces `X-Tier` |

New dependencies to justify when added: `redis`, a PostgreSQL driver
(`psycopg`), and the LLM client chosen for intent parsing.

Later: WhatsApp (Meta Cloud API) + Telegram, kitchen notifications (Telegram
group), customer status updates, Italian e-invoicing (`fatturazione elettronica`).

---

## 5. Business context

| Field | Value |
|---|---|
| Operator | PULSAR SERVICE S.A.S., Monza, Italy (ATECO 62.01.00) |
| Customer | small / medium Italian pizzerias |
| Price | €49–99 / month per restaurant |
| VAT | 22% |

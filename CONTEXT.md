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

1. **Guided flow, not free chat** *(decided 2026-10-15)*. The bot walks the
   customer through predefined steps using buttons / lists. There is **no LLM
   in v1**: no intent parsing, no prompt, no model-generated text.
   - Steps (draft — final shape comes from real menus):
     `start → category → item → size → extras → quantity → "add more?" →
     cart review → pickup / delivery → time slot → address (delivery only) →
     kitchen notes → confirm`. Every step offers **back** and **cancel**.
   - Free text typed outside the notes step → reply "please use the buttons"
     and re-show the current step.
   - **Kitchen notes** is the last step before confirm: optional free text
     (e.g. "senza cipolla"), stored on the order and forwarded to the kitchen
     verbatim. It is never parsed and never changes price or items. Length
     cap + control-character stripping apply; it is PII-bearing (GDPR).
   - Core is a channel-agnostic **state machine** keyed by
     `(restaurant_id, channel, customer)`. It emits abstract screens
     (text + options); each channel adapter renders them. WhatsApp
     interactive messages have tight limits (few reply buttons, short list
     messages — **verify current limits in Meta docs**), so categories and
     pagination are required for menus larger than one list.
2. **Money comes only from the database.** Prices, availability, cart totals
   and IVA are computed from menu rows, never from customer input.
   Money is `Decimal` (2 places, half-up) end to end. Menu prices are
   IVA-inclusive; `Order` extracts IVA from the gross total (`vat_eur`,
   `net_eur`, `net + vat == total`). `vat_rate` defaults to 22% but is a
   per-order field — **verify the rate**: Italian food service/takeaway is
   usually 10%, 22% is the standard rate.
3. **Multi-tenant from day one.** Every menu row, cart, order, and log line
   carries a `restaurant_id`. No shared global state across restaurants.
4. **Real authentication.** Restaurant operators authenticate (API key or
   token per restaurant). Customers are identified by channel + phone.

Superseded: *"The LLM only parses intent and calls tools"* and *"No RAG — the
menu fits in the LLM context"* (2026-10-15, replaced by decision 1).

---

## 3. Known gaps (fix before production)

| Area | Issue |
|---|---|
| `security/tiers.py` + `rate_limiter.py` | Tier comes from the client-sent `X-Tier` header → anyone can claim `agent`. Tiers are real-estate names (`anonymous/lead/agent`). Replace with authenticated roles (`customer / restaurant / admin`). Webhooks must use `allow_sender(restaurant_id, sender_id)`; `enforce_rate_limit` (per IP) is for the operator HTTP API only. |
| `security/prompt_guard.py` | No longer needed with the guided flow (no LLM). Keep out of the request path; delete or repurpose once the flow is built. |
| `security/audit.py` | Stores a 120-char raw text preview (PII) and no `restaurant_id`. Fields are real-estate/RAG-shaped (`question`, `sources_count`). |
| `security/pii_masker.py` | Phone regex also masks order numbers (e.g. `20261015`); PII keys are real-estate (`agent_phone`). |
| `security/upload/*` | User-facing error messages are in Arabic — operators are Italian. |
| Data model | `CartItem` references dishes by name; needs `menu_item_id` + price snapshot, sizes / extras, pickup vs delivery, address, delivery fee, time slot, kitchen notes. Menu needs `allergens` (EU Reg. 1169/2011) — shown from DB only. |
| GDPR | Customer phone numbers, addresses and kitchen notes are PII. Retention policy must be decided. |
| Fiscal | Confirm with an accountant: IVA rate, and whether the bot only takes orders while the restaurant issues the receipt from its own till. |

---

## 4. Plan (next)

Ordered by risk — cheapest checks first:

1. **Real menus** from 2–3 pizzerias (ideally one pilot restaurant). They
   drive the data model and the shape of the steps.
2. **Data model + flow diagram** on paper: menu, sizes, extras, allergens,
   cart, order, delivery; every step, its options, next step, back / cancel.
   Draft: [docs/design-v1.md](docs/design-v1.md) (built from a synthetic
   menu — no real menus yet; assumptions tagged [A#]).
3. **State machine** in pure Python — no channel, no DB — fully tested.
4. **Telegram + PostgreSQL** end to end for one restaurant: menu import via
   `security/upload` → `menu/normalizer`, cart, checkout, kitchen
   notification (Telegram group).
5. **Generalize:** per-restaurant auth (replaces `X-Tier`), audit, GDPR
   retention, then **WhatsApp** (Meta Cloud API).

In parallel (not code): start WhatsApp Business verification early; accountant
on IVA + fiscal scope.

Open: cart storage — a PostgreSQL `carts` table with `expires_at` is
recommended over Redis at this scale (one less service).

New dependencies to justify when added: a PostgreSQL driver (`psycopg`), the
Telegram client. No LLM client.

Later: customer status updates, WhatsApp, Italian e-invoicing only if the
fiscal check says it is needed.

---

## 5. Business context

| Field | Value |
|---|---|
| Operator | PULSAR SERVICE S.A.S., Monza, Italy (ATECO 62.01.00) |
| Customer | small / medium Italian pizzerias |
| Price | €49–99 / month per restaurant |
| VAT | 22% |

# Calivio (كود: PizzaBot) — ملخص المشروع الموحّد

> آخر تحديث: 2026-10-18 · آخر commit كود على `main`: `ef35f9a` (Telegram adapter + إشعار المطبخ) · تجربة LLM على `experiment/local-llm`: `b7a81f2` (local بس، مش pushed)
> الملف ده بيوحّد النسخة القديمة من `PROJECT_SUMMARY.md` مع `docs/strategy/IDEAS_2026-10-17.md` و `docs/strategy/VISION_2026-10-18.md`.
> الملفين دول لسه مرجع للتفاصيل، بس **لو فيه تعارض، الملف ده هو الصح.**
> ✅ = متفق عليه أو خلص · ❓ = قرار مفتوح · 🔒 = قرار ثابت ماتتغيرش من غير قرار صريح

---

## 1. المشروع إيه

**Calivio** = مدير رقمي للبيتزاريا الصغيرة: بياخد الطلبات، وبيقرا الفواتير، وبيحوّل الشغل اليومي لأرقام وقرارات على WhatsApp، **من غير ما صاحب المطعم يدخل أي داتا**.

- **الشركة:** PULSAR SERVICE S.A.S. — Monza (ATECO 62.01.00)
- **البراند:** Calivio (المنصة) · Calivio Cucina (المطاعم) ❓ بعد بحث العلامة واختبار الاسم
- **PizzaBot:** اسم الـ repo والكود بس
- **الزبون (ICP):** بيتزاريا asporto + دليفري، صاحبها بيشغّلها، 300–1500 طلب/شهر، Monza-Brianza وشمال Milano
- **لغة الزبون:** إيطالي بس في v1
- **مش gestionale:** ❌ كاشير/RT · ❌ صالة وترابيزات · ❌ مرتبات ومحاسبة · ❌ أي إدخال يومي

اتفصل يوم 2026-10-15 عن مشروع Madaar، ومفيش أي import منه.

### الطبقات الخمسة ✅ (التفاصيل في `docs/strategy/VISION_2026-10-18.md` §3–4)

1. **الالتقاط (Capture):** طلبات (تليفون، WhatsApp، ويب)، فواتير الموردين، الوصفات (مرة واحدة)، ولاحقاً ليبلات وحساسات (PizzaBox). **← الكود الحالي هو أول جزء هنا (الطلبات).**
2. **الفهم (المستوى 1):** حساب وقواعد، دقيق 100%، من أول أسبوع. ~60–70% من القيمة.
3. **التنبؤ (المستوى 2):** forecasting من غير LLM. محتاج 2–3 شهور تاريخ، والمطعم الجديد يتعلم من المطاعم اللي شبهه.
4. **الاقتراح (المستوى 3):** النظام يقترح وصاحب المطعم يوافق بضغطة. **مؤجل.**
5. **المدير (المستوى 4):** يراقب ويقرر وينفّذ في حدود قواعد المطعم، ويبلّغ بعدها. **رؤية بعيدة.**

- **الواجهة:** WhatsApp أساساً، مش dashboards.
- **دور الـ LLM (مستقبلاً):** فهم المدخلات الفوضوية وشرح القرارات. **الأرقام دايماً من الداتا والخوارزميات.**

---

## 2. القرارات الثابتة 🔒

1. **Guided flow بالزراير، ومفيش LLM في v1.**
   - النص الحر في خطوتين بس: **العنوان** و**ملاحظات المطبخ** (لحد 300 حرف، control chars بتتشال، مابتأثرش على السعر أو الأصناف).
   - أي نص تاني → "Usa i pulsanti qui sotto 👇" + نفس الشاشة. أي رسالة مش نص (صورة، sticker، voice…) → event `Other`، ومابتتاخدش أبداً كعنوان أو ملاحظة.
   - الـ core = **state machine** مستقلة عن الـ channel، بتطلّع screens مجردة.
   - ❓ v2: الـ LLM ممكن يتسمح **كـ parser بس** (يطلّع IDs بتتراجع على الـ DB، من غير سعر ولا نص للزبون، والتأكيد بالأزرار). **لسه مش قرار** — مستني نتيجة تجربة 2 (§7).
2. **الفلوس من الـ database بس.** `Decimal` بخانتين و half-up. أسعار المنيو شاملة الـ IVA، و `net + vat == total`.
   - `vat_rate` **لكل مطعم في الـ DB ومن غير default** (`restaurants.vat_rate NOT NULL`). الـ storage بيرفض مطعم من غيره (`ConfigError`) ومابيرجعش أبداً للـ 22% اللي في Python، وكل order بياخد نسخة منه.
   - مطعم الـ demo على **10%** (`DEMO_VAT_RATE` في `seed_demo.py`) ❓ مستني تأكيد المحاسب (10% للأكل takeaway/delivery ولا 22%).
3. **Multi-tenant من أول يوم.** `restaurant_id` في كل صنف وcart وorder وlog، والـ foreign keys كلها composite بالـ `restaurant_id`.
4. **Authentication حقيقي.** كل مطعم بـ token، والزبون بالـ channel + رقم التليفون/chat id. (لسه: التليفون مابيتطلبش، §6.)
5. **الأرقام والتحليلات دايماً من الداتا والخوارزميات، مش من LLM.**
6. **فلوس الزبون ماتعديش على PULSAR** (✅ كاتجاه، ❓ تأكيد محاسب). v1 = الدفع عند الاستلام بس.

---

## 3. البيئة

| | |
|---|---|
| اللغة | Python 3.14، Pydantic v2 |
| Dependencies | pydantic, fastapi, pandas, openpyxl, puremagic, pypdf, pytest, `psycopg[binary]==3.3.6` (pinned في `requirements.txt`). الـ Telegram client معمول بـ stdlib (urllib + json) من غير dependency |
| Repo | https://github.com/mohamdhamed/PizzaBot (`main`) |
| جهاز التطوير | Fedora، `/mnt/windows/PizzaBot`، من غير Docker/PostgreSQL |
| السيرفر | Oracle Cloud Always Free، Ubuntu، **aarch64**، 23GB RAM، SSH alias `pulsar` (user `ubuntu`) |
| المشروع على السيرفر | `~/PizzaBot` + `.venv`. **411 test بيعدّوا في الـ container (من غير skip)**، وعلى الـ host 342 بيعدّوا و 69 skipped (tests الـ DB محتاجة `DATABASE_URL`) |
| Docker | compose project `pizzabot`: `db` (postgres:17، volume `pizzabot_pgdata`، network `pizzabot_net`) و `app` (python:3.14-slim، user مش root، `restart: unless-stopped`). **مفيش ports منشورة.** الـ `app` بيعمل migrate وبعدين long polling لـ Telegram |
| البوت | `@Pizzabotit_bot`، شغال دلوقتي لمطعم `demo`، و**مفتوح لأي حد يلاقيه** (§9) |
| Claude Code | `~/.local/bin/claude` جوه `tmux` بـ `--remote-control` |

**ماتتلمسش:** `BotBudget`, `openclaw`, `postgres` على نفس السيرفر.
**على السيرفر كمان:** volume `pizzabot_ollama_models` (~6.6GB) من تجربة 1 — ممكن يتمسح لو تجربة 2 حسمت الموضوع.

```bash
docker compose up -d --build                                    # db + البوت
docker compose logs -f app                                      # logs (من غير نصوص الزباين)
docker compose run --rm app python -m pytest tests -q           # كل الـ tests مع PostgreSQL
docker compose run --rm app python -m pizzabot.db.seed_demo     # مطعم الـ demo
docker compose run --rm app python -m pizzabot.db.set_kitchen_chat demo <chat_id>
docker compose run --rm --no-deps app python -m pizzabot.channels.telegram.find_chats   # (والبوت واقف)
.venv/bin/python -m pytest tests/ -q                            # على الـ host
.venv/bin/python -m pizzabot.simulator                          # http://127.0.0.1:8765
```

الـ secrets (`POSTGRES_PASSWORD`, `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`) في `.env` بس (mode 600، في `.gitignore`)، والقالب `.env.example`.

---

## 4. هيكل الكود (`main` @ `ef35f9a`)

```
migrations/                 0001_init.sql (الـ schema) · 0002_kitchen_notify_asap.sql
pizzabot/
├── config.py               ROOT / DATA_DIR / QUARANTINE_DIR
├── service.py              ★ process_event: حدث واحد في transaction واحدة (lock → handle → save_order → save)
├── storage/                psycopg من غير ORM: menu/settings، conversations (FOR UPDATE + expiry 2h)،
│                           orders (snapshot، booked_slots، حالة إشعار المطبخ)
├── db/                     migrate (advisory lock + checksums)، ping، seed_demo، set_kitchen_chat
├── channels/telegram/      ★ api (urllib، retry/backoff/429)، render (keyboard + pagination)،
│                           bot (long polling)، kitchen (رسالة المطبخ + retry)، find_chats، __main__
├── flow/
│   ├── machine.py          ★ handle(conversation, event, ctx) -> Result
│   ├── menu.py             Menu, Category, MenuItem, OptionGroup, Option, RestaurantSettings
│   ├── slots.py            مواعيد الفتح + pickup/delivery slots
│   └── demo.py             مطعم demo
├── simulator.py            web chat محلي (stdlib، 127.0.0.1)
├── order/schema.py         Order, CartItem, CartOption, OrderStatus, Channel, Fulfilment
├── menu/normalizer.py      أعمدة Excel (it/en/ar) → dish_name, price_eur, category, available, ingredients
└── security/
    ├── upload/             فحص الملفات (policy, magic bytes, xlsx/pdf safety, quarantine)
    ├── tiers.py            ⚠ §9
    ├── rate_limiter.py     sliding window لكل (tier, IP) + لكل sender
    ├── prompt_guard.py     ⚠ مش مستخدم
    ├── pii_masker.py       ⚠ §9
    └── audit.py            ⚠ §9
tests/                      test file لكل module؛ tests/db/ ضد PostgreSQL حقيقي (DB مؤقتة بتتمسح)
docs/design-v1.md           data model + flow (assumptions [A1]–[A10])
docs/strategy/              IDEAS_2026-10-17.md، VISION_2026-10-18.md (مرجع)
CONTEXT.md                  القرارات والـ gaps
```

### الـ state machine
- **Pure:** من غير I/O ولا clock (`ctx.now`) ولا randomness. الـ caller بيحمّل ويحفظ ويعرض وبيطبّق الـ effects.
- **Events:** `Choice(id)` و `Text(text)` و `Other()` (أي رسالة مش نص). **الناتج:** `Result(conversation, screen, effects)`.
- `Screen(text, options, accepts_text)`، `ScreenOption(id, label)` — id قصير ASCII (يدخل في `callback_data` 64 byte).
- **Effect:** `PlaceOrder(order)`. **`FlowContext`:** `settings`, `menu`, `now`, `booked_slots`.
- **`Conversation`:** Pydantic/JSON، فيه `state`, `state_data`, `cart: list[CartLine]` (ids بس، من غير أسعار).
- **رقم الطلب:** الـ DB هي اللي بتدّيه، والـ service بتسجله بـ `record_order_code` وتعيد `render`، فشاشة `done` بتقول "Ordine n. 001 inviato! ✅".
- **"Prima possibile" (decision A):** عند التأكيد بياخد **أقرب slot لسه فيه مكان** (`slot_at` + `asap=true`)، فبيتحسب في السعة. لو مفيش slot فاضل النهارده: رسالة قصيرة ويرجع لاختيار الـ slot، من غير طلب. ولو كل الـ slots مليانة، الزبون مايقدرش يبدأ طلب (`closed`).
- الزراير القديمة → "Questa scelta non è più valida." + الشاشة الحالية.
- المنيو والحد الأدنى والـ slot بيتراجعوا عند الـ cart والملخص والتأكيد.

**States:** `start → (closed) → category → item → item_detail → [option_group → quantity] → cart_review → edit_line → fulfilment → address (delivery) → slot → notes → confirm → done`

- كل شاشة (ما عدا `start`) فيها ⬅ Indietro و ✖ Annulla.
- **Quick add:** "➕ Aggiungi / ×2 / ×3" بالنسخة الـ standard وبيرجع لقايمة الأصناف بـ "✓ Aggiunto: …". "⚙️ Personalizza" للـ options.
- شاشة الأصناف: 🛒 Carrello (العدد والإجمالي) و 📂 Altre categorie.
- الـ standard options مخفية من السطور اللي الزبون بيشوفها (بس بتظهر للمطبخ).
- **السعر:** `unit = base_price + Σ price_delta`.
- **Allergens:** الـ 14 بتوع EU (Reg. 1169/2011)، من الـ DB بس.

### الـ data model في PostgreSQL (`migrations/`)
```
restaurants, categories, menu_items, option_groups, options, item_option_groups
opening_hours, slot_settings
conversations   unique(restaurant_id, channel, customer_ref); state, state_data jsonb, cart jsonb, expires_at (2h idle)
order_counters  (restaurant_id, business_date) → last_seq
orders          public_code ('001'، لكل مطعم في كل يوم عمل)، fulfilment، address، slot_at، asap، kitchen_notes،
                totals، vat_rate/vat_eur/net_eur، status، kitchen_notified_at
order_lines, order_line_options   ← snapshots للأسماء والأسعار
```
- Ids قصيرة ASCII (لحد 48 حرف)، والـ primary key `(restaurant_id, id)`، والـ foreign keys composite بالـ `restaurant_id`.
- الفلوس `NUMERIC(10,2)`، مع CHECKs: `items + fee = total` و `net + vat = total`.
- `public_code` بيتعمل بـ trigger (`BEFORE INSERT`) من `order_counters` حسب timezone المطعم.
- الـ cart في PostgreSQL مع `expires_at` (من غير Redis). الأسماء عامة (`menu_item` مش `pizza`) عشان التوسع لقطاعات تانية.

### Double confirm والتزامن
- **نفس الزبون:** `load_conversation(..., for_update=True)` بيقفل الـ row (وبيعمل row منتهي الأول لو مش موجود). الـ "Conferma" التانية بتلاقي `state=done` فترجع `MSG_STALE`، ومفيش طلب تاني.
- **زباين مختلفين على نفس الـ slot:** `pg_advisory_xact_lock(0x505A4F52, hashtext(restaurant_id))` قبل التأكيد، فالتأكيدات بتمشي واحد ورا التاني لكل مطعم. ده بيغطي "prima possibile" كمان.
- اتعمل mutation test: شيل أي lock من الاتنين بيوقّع test.
- الـ session بتاعة البوت فيها `lock_timeout=2s` و `statement_timeout=5s`. لو حصل timeout، الزبون بيشوف "Riprova tra un attimo 🙏" والـ loop بيكمل.

### الـ Telegram adapter (`pizzabot/channels/telegram/`)
- **Long polling** (`getUpdates` بـ offset)، مطعم واحد من `RESTAURANT_ID`. لو ناقص أو مش في الـ DB، أو الـ token مرفوض: exit 2 فوراً.
- **Private chats بس.** الـ groups بتتجاهل، وجروب المطبخ بيستقبل الإشعارات بس. `customer_ref = from.id`.
- `callback_query` → `Choice(data)` بعد التأكد إن الـ data ≤ 64 byte. **`answerCallbackQuery` بيتبعت دايماً.** الرسالة اللي اتداس عليها بتتعدل (`editMessageText`)، ولو فشل التعديل بتتبعت رسالة جديدة.
- `/start` → شاشة البداية بـ conversation جديدة (`process_event(..., restart=True)` بيمسح الـ cart، زي Annulla). أي `/command` تاني أو رسالة مش نص → `Other`.
- **Pagination** (أكتر من 8 options): زراير `~pg:<n>`، والـ machine ids مابتبدأش أبداً بـ `~`. تقليب الصفحة مابيعدّيش على الـ machine: آخر screen لكل chat متخزنة في الذاكرة وبتترسم تاني.
- **Logs:** update ids، الـ restaurant id، نوع الحدث، أرقام الطلبات، وأنواع الأخطاء بس. مفيش نص رسايل ولا عناوين ولا تليفونات ولا ملاحظات ولا user ids. الـ token مابيظهرش في exceptions ولا logs (وفيه redact filter كخط دفاع أخير).

### إشعار المطبخ
- بعد الـ commit، رسالة لـ `restaurants.kitchen_telegram_chat_id`، مبنية من الـ snapshots في الـ DB، بالإيطالي: الرقم، Ritiro/Consegna + الميعاد (أو "Prima possibile (entro HH:MM)")، السطور بالـ options والكميات، "⚠️ NOTE PER LA CUCINA"، والعنوان للـ delivery، والإجمالي.
- `kitchen_notified_at` بيتكتب بس بعد ما Telegram يأكد. الفشل مابيأثرش على الطلب، والـ polling loop بيعيد المحاولة بـ backoff (5s → … → 5 دقايق). ده at-least-once.
- لو مفيش kitchen chat: warning واضح فيه الأمر اللي يتشغّل، والطلبات بتفضل pending. لو الجروب اتحوّل لـ supergroup، الـ id الجديد بيتحفظ لوحده.
- طلبات الأيام اللي فاتت مابتتبعتش (نفس اليوم بس، [A9])، وبيطلع عنها warning.
- **لسه:** أزرار الحالة في الجروب (`✅ Accetta · 👨‍🍳 In preparazione · 📦 Pronto · 🛵 Consegnato · ❌ Rifiuta` → `OrderStatus`).

---

## 5. اللي خلص

**على `main`:**
1. Initial standalone project
2. Order schema: Decimal، `restaurant_id`، تفصيل الـ IVA
3. parsing الأسعار الإيطالية (`7,50`)، Excel formula false positives، rate limit لكل sender
4. اعتماد الـ guided button flow وشيل الـ LLM من v1
5. `docs/design-v1.md`
6. الـ state machine + توسيع `Order` للـ checkout (fulfilment, address, slot, notes, delivery fee)
7. الـ simulator + مطعم الـ demo
8. Quick add بالـ standard options
9. زرار "Altre categorie"
10. إخفاء الـ standard options (`d5fa2c5`)
11. ✅ Docker Compose: app + PostgreSQL 17 (`394d1ce`)
12. ✅ Schema أولي + migration runner (`deb9c2c`)
13. `PROJECT_SUMMARY.md` (`6795321`)
14. ✅ Storage layer + `process_event` في transaction واحدة، seed_demo، double-confirm locks (`c1be304`)
15. ✅ Telegram adapter (long polling، مطعم واحد) + إشعار المطبخ + رقم الطلب في شاشة done + prima possibile بياخد slot + event `Other` (migration 0002، `ef35f9a`)

**على `experiment/local-llm` (مش هيتعمله merge، ومش pushed):**
- تجربة 1: LLM محلي كـ parser (`b7a81f2`) — §7

---

## 6. الخطة الجاية

### ✅ خلص: Channel + PostgreSQL end-to-end لمطعم واحد
Docker + schema + storage + Telegram adapter + إشعار المطبخ + ASAP بيحجز slot. **فاضل منها:**
- تجربة يدوية ببوت حقيقي وجروب مطبخ حقيقي (الخطوات في تقرير الـ commit `ef35f9a`).
- أزرار حالة الطلب في جروب المطبخ (§4).
- استيراد المنيو: Excel → `security/upload` → `menu/normalizer` → DB.
- حدود memory/CPU للـ containers في `docker-compose.yml` (لسه مش محطوطة).

### الخطوة الجاية ⏳: رقم تليفون الزبون + allowlist مؤقتة
1. **خطوة التليفون في الـ flow:** قبل التأكيد (للـ delivery على الأقل ❓ ولا للكل)، عن طريق زرار `request_contact` في Telegram. الرقم بيتحفظ على الـ order ويظهر في رسالة المطبخ بدل الـ link. والـ state machine تفضل pure (التليفون بيوصل كـ event جديد من الـ adapter).
2. **Allowlist مؤقتة:** البوت حالياً مفتوح لأي حد. لحد ما يبقى فيه auth ومطعم pilot، يرد بس على Telegram user ids متسجلة (في `.env` أو جدول). اللي مش في الـ list ياخد رسالة قصيرة، وده بيتسجل في الـ log من غير PII.

### بعد كده
- **WhatsApp** (Meta Cloud API) — القناة الأساسية للزبون في إيطاليا. **الـ Business verification يبدأ دلوقتي بالتوازي** عشان بياخد وقت. نفس الـ state machine ونفس `process_event`، والـ adapter بس هو اللي جديد (webhook، وحدود الأزرار والـ lists بتاعة Meta ❓ تتأكد من الـ docs).
- **طبقة قراءة الفواتير (الفهم، المستوى 1):** الفواتير XML (FatturaPA) + الوصفات → مكسب كل بيتزا، مصفوفة المنيو، أسعار الموردين، تقرير أسبوعي على WhatsApp. **ترتيبها وأولويتها بتتحدد بنتيجة الـ Analisi gratuita.**
- **"Nessuna telefonata persa":** inoltro di chiamata لرقم VoIP → رسالة صوتية + لينك طلب على WhatsApp/SMS + "Richiamami" (من غير LLM).
- Auth لكل مطعم (بدل `X-Tier`)، audit، GDPR retention.
- Pre-order نفس اليوم + وضع Pausa ❓
- **التنبؤ:** بعد 2–3 شهور داتا.
- **المخزن والهاردوير:** ليبلات، PizzaBox، HACCP.

### بالتوازي (مش كود)
- **Analisi gratuita يدوي:** فواتير شهر + منيو → "بتكسب كام على كل بيتزا". أداة بيع واختبار سوق، و**هي اللي بتحدد إمتى نبني طبقة الفواتير**.
- **Meta WhatsApp Business verification** — يبدأ دلوقتي.
- 3 مطاعم pilot · منيوهات حقيقية لاختبار [A1]–[A10]
- محاسب: نسبة الـ IVA، نطاق الإيصال، الدفع
- avvocato privacy: DPA (art. 28)، بند الداتا المجمّعة
- بحث العلامة Calivio على UIBM و EUIPO (فئات 9، 35، 42)
- ترقية Oracle لـ Pay As You Go + budget alert ❓ · backup يومي برة السيرفر قبل أول مطعم يدفع

---

## 7. الـ LLM: الحالة

**تجربة 1** (local، `b7a81f2`):
| | qwen2.5 3B | qwen2.5 7B |
|---|---|---|
| الطلب كله صح | 6/20 | 15/20 |
| السطور صح | 53% | 84% |
| IDs مش موجودة | 9 | 2 |
| Injection | فشل | نجح |
| latency avg / p95 | 5.3s / 7.7s | 12.9s / 20.6s |
| 4 رسايل مع بعض (avg) | 86s | 204s |
| RAM | 3.1 GiB | 5.8 GiB |

الأمان شغال (كل ID غلط اتمسك). المشكلة السرعة → **v1 من غير LLM.**

**تجربة 2** ⏳ (الـ prompt مكتوب، **ولسه ماتنفذتش على السيرفر**): Matcher / LocalEnum (7B) / Api (`claude-haiku-5-5`) / Hybrid على نفس الـ 20 حالة → `REPORT_COMPARISON.md` على نفس الـ branch. التصميم: interface واحد `OrderParser`.

---

## 8. التجاري (تقديرات ❓)

| الباقة | فيها إيه | السعر/شهر |
|---|---|---|
| Ordini | تليفون + WhatsApp + المطبخ + تقرير أسبوعي | ~€59 |
| **Gestione** ⭐ | + الفواتير + مكسب كل بيتزا + الزباين + الموردين + التوقع | ~€119 |
| Completo | + PizzaBox (comodato) + ليبلات | ~€179 |

تركيب €150–300 (يتلغى لأول العملاء)، شهري من غير عقد طويل. **الهدف الأول: 50 مطعم.**
اشتراك PULSAR منفصل عن فلوس الزبون: فاتورة SDI شهرية + SEPA/Stripe Billing.

---

## 9. مشاكل معروفة (قبل الـ production)

| المكان | المشكلة |
|---|---|
| البوت مفتوح | `@Pizzabotit_bot` شغال وأي حد يلاقيه يقدر يعمل طلبات demo → allowlist مؤقتة (§6). |
| رقم تليفون الزبون | الـ flow مابيسألش عنه، فرسالة المطبخ للـ delivery فيها link للـ Telegram بتاع الزبون بداله → خطوة التليفون (§6). |
| `tiers.py` + `rate_limiter.py` | الـ tier من header `X-Tier` (أي حد يدّعي إنه `agent`)، والأسماء بتاعة العقارات. → roles `customer / restaurant / admin`، والـ webhooks تستخدم `allow_sender(restaurant_id, sender_id)`. الـ rate limit لسه مش متوصّل بالبوت. |
| `prompt_guard.py` | مالوش لازمة من غير LLM → يتشال من الـ request path أو يتحذف. |
| `audit.py` | preview 120 حرف من النص الخام (PII)، من غير `restaurant_id`، وfields بتاعة RAG. |
| `pii_masker.py` | الـ regex بيخفي أرقام الطلبات (`20261015`)، و keys العقارات (`agent_phone`). |
| `security/upload/*` | رسايل الخطأ بالعربي → إيطالي. |
| `order/schema.py` | `VAT_RATE = 0.22` لسه default في الـ Python model. الـ storage مابيستخدموش أبداً (بياخد النسبة من المطعم)، بس الأحسن يتشال. |
| Pagination | آخر screen لكل chat في الذاكرة. بعد restart، زراير الصفحات القديمة بتعرض الشاشة الحالية مع "scelta non più valida". مقبول مع instance واحدة، لكن مع webhook (WhatsApp) أو أكتر من instance لازم تتخزن في الـ DB. |
| Replay بعد crash | الـ updates اللي ماتأكدتش بتتبعت تاني. الزراير المتكررة بترجع "stale" (من غير طلب مكرر)، لكن نص اتكتب ممكن يترد عليه مرتين. |
| إشعار المطبخ | at-least-once: لو Telegram أكد والـ process وقع قبل الـ UPDATE، الرسالة بتتبعت تاني. الـ backoff في الذاكرة، والمحاولة كل دورة polling (لحد ~25s). |
| Timeouts | `lock_timeout=2s` و `statement_timeout=5s` لسه ماتجربوش تحت ضغط. |
| Tests | لما test DB يفشل، pytest بيطبع الـ URL بتاع الـ DB المؤقتة بالباسورد في الـ terminal (مش في الـ logs ولا الـ git) → يتعمله redact. |
| GDPR | العناوين والملاحظات والـ Telegram ids (وبعدين التليفونات) PII → سياسة احتفاظ (الـ conversations المنتهية والطلبات القديمة لسه مابتتمسحش). |
| Fiscal | نسبة الـ IVA ونطاق الإيصال → محاسب. |
| الاعتمادية | Oracle Free من غير SLA → backup + monitoring + fallback برقم المطعم. |

### Assumptions مفتوحة
[A1] 20–60 صنف، 4–8 categories · [A2] الحجم والعجينة بفرق ثابت · [A3] الـ extras سعر ثابت · [A4] "senza …" ببلاش في الملاحظات · [A5] مفيش metà e metà / al metro · [A6] الدفع عند الاستلام · [A7] إيطالي بس · [A8] الـ allergens من المشغّل · [A9] نفس اليوم بس · [A10] delivery zones بالـ CAP

---

## 10. قرارات مفتوحة ❓ (بالأولوية)

1. **المدخل وقناة الزبون الأولى:** الطلبات + التليفون/WhatsApp أولاً، ولا الفواتير + food cost؟ (الـ Analisi gratuita هتحسمها). Telegram دلوقتي للتجربة الداخلية والمطبخ، والقناة الأساسية للسوق WhatsApp.
2. **رقم التليفون:** نطلبه للـ delivery بس ولا لكل الطلبات؟ وشكل الـ allowlist المؤقتة (`.env` ولا جدول).
3. LLM كـ parser في v2 (بعد تجربة 2) → لو اتوافق، يتكتب في `CONTEXT.md` و `docs/design-v1.md`.
4. Pre-order نفس اليوم + Pausa في v1؟
5. الدفع أونلاين في v2: حساب لكل مطعم ولا Stripe Connect؟ (محاسب)
6. اعتماد اسم Calivio (بحث العلامة + اختبار مع أصحاب بيتزاريات).
7. الأسعار النهائية · هيكل العقد · طريقة الوصول للفواتير (commercialista / export).
8. شكل صفحة الويب (أزرار ولا منيو بسلة) — لو اتعملت.
9. ترقية Oracle لـ PAYG · PizzaBox prototype إمتى.

أسئلة منتج:
- شكل الـ delivery zones، اسم الزبون (نسأله ولا من الـ profile)، البيرة في الدليفري.
- "Prima possibile" بيوعد بأقرب slot بعد `min_lead_minutes` ("entro 20:30"): هل ده الوعد الصح؟
- طلبات الأيام اللي فاتت اللي ماوصلتش للمطبخ: تتبعت ولا تتسجل بس؟ (الحالي: بتتسجل بس)
- `/start` في نص الطلب بيمسح الـ cart: مقبول ولا نخليه يكمّل من نفس المكان؟

---

## 11. تعليمات لـ Claude chat وهو بيكتب prompts لـ Claude Code

- الـ prompts هتتنفذ بـ **Claude Code** على السيرفر في `~/PizzaBot` (عنده الكود والـ terminal و git).
- كل prompt = **خطوة واحدة صغيرة** بتعريف واضح لـ "done" (tests بتعدّي أو أمر شغال).
- احترم §2: مفيش LLM على `main`، الأسعار من الـ DB، `restaurant_id` في كل حتة، `Decimal`.
- الـ state machine تفضل **pure**. الـ I/O (DB، قنوات، إشعارات) في layers بره `pizzabot/flow/`.
- كل خطوة فيها tests، وكل الـ tests تعدّي قبل الـ commit (في الـ container، من غير skip)، و commit message واضح.
- Docker images تدعم **arm64**، وماتلمسش المشاريع التانية على السيرفر.
- الـ secrets في `.env` (في `.gitignore`)، ومايتعملهاش commit أبداً.
- أي dependency جديدة تتبرر وتتعمل pinned.
- نص الزبون بالإيطالي، والكود والـ comments بالإنجليزي.
- الأسماء في الكود عامة (`menu_item`، `restaurant`) — مش خاصة بالبيتزا.

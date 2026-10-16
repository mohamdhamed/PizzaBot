# PizzaBot — ملخص المشروع (للاستخدام في Claude chat)

> آخر تحديث: 2026-10-16 · آخر commit: `d5fa2c5`
> الملف ده معمول عشان يتلصق في أول محادثة مع Claude chat، علشان يكتب prompts للخطوات الجاية
> اللي هتتنفذ بـ Claude Code على السيرفر.

---

## 1. المشروع إيه

بوت لاستقبال الطلبات للبيتزاريات الإيطالية الصغيرة والمتوسطة، على **Telegram** الأول وبعدين **WhatsApp**.

- **الشركة المشغّلة:** PULSAR SERVICE S.A.S. — Monza, Italy (ATECO 62.01.00)
- **العميل:** البيتزاريات الصغيرة/المتوسطة في إيطاليا
- **السعر:** €49–99 في الشهر لكل مطعم
- **لغة الزبون:** إيطالي بس في v1

اتفصل يوم 2026-10-15 عن مشروع تاني (Madaar، RAG للعقارات المصرية)، ومفيش أي import منه.

---

## 2. القرارات الثابتة (locked — ماتتغيرش من غير قرار صريح)

1. **Guided flow بالزراير، مش free chat.** **مفيش LLM خالص في v1**: لا intent parsing ولا prompts ولا نص بيولّده model.
   - النص الحر مسموح بس في خطوتين: **العنوان** (delivery) و**ملاحظات المطبخ** (مثلاً "senza cipolla").
   - أي نص في أي خطوة تانية بيرد عليه "Usa i pulsanti qui sotto 👇" ويعيد الشاشة.
   - ملاحظات المطبخ: لحد 300 حرف، والـ control chars بتتشال، وبتتبعت للمطبخ زي ما هي، ومابتأثرش أبداً على السعر أو الأصناف.
   - الـ core عبارة عن **state machine** مستقلة عن الـ channel، بتطلّع "screens" مجردة (نص + options)، وكل channel adapter بيعرضها بطريقته.
2. **الفلوس من الـ database بس.** الأسعار والتوفر والإجمالي والـ IVA بتتحسب من الـ menu، مش من input الزبون أبداً.
   - `Decimal` بخانتين عشريتين و half-up في كل حتة.
   - أسعار المنيو شاملة الـ IVA، والـ `Order` بيطلّع الـ IVA من الإجمالي (`net + vat == total`).
   - `vat_rate` الافتراضي 22%، لكنه field في كل order. **محتاج تأكيد من محاسب** (الأكل غالباً 10%).
3. **Multi-tenant من أول يوم.** كل صنف وcart وorder وlog فيه `restaurant_id`، ومفيش shared global state.
4. **Authentication حقيقي.** كل مطعم بـ API key/token، والزبون بيتعرّف بالـ channel + رقم التليفون/chat id.

---

## 3. البيئة

| | |
|---|---|
| اللغة | Python 3.14، Pydantic v2 |
| Dependencies | pydantic, fastapi, pandas, openpyxl, puremagic, pypdf, pytest (كلها pinned في `requirements.txt`) |
| Repo | https://github.com/mohamdhamed/PizzaBot (branch `main`) |
| جهاز التطوير | Fedora، `/mnt/windows/PizzaBot`، ومفيش Docker/PostgreSQL عليه |
| السيرفر | Oracle Cloud، Ubuntu، **aarch64 (ARM)**، 23GB RAM، الـ SSH alias `pulsar` (user `ubuntu`) |
| المشروع على السيرفر | `~/PizzaBot` مع `.venv` شغال، والـ 263 test بيعدّوا |
| Claude Code على السيرفر | متسطب (`~/.local/bin/claude`)، وبيشتغل جوه `tmux` بـ `--remote-control` عشان أوصله من الموبايل |
| الـ Deployment المخطط | كل حاجة في **Docker** على السيرفر، و PostgreSQL جواه (images لازم تدعم arm64) |

على السيرفر برضه في مشاريع تانية (`BotBudget`, `openclaw`, `postgres`)، **ماتتلمسش**.

الأوامر:

```bash
.venv/bin/python -m pytest tests/ -q        # tests
.venv/bin/python -m pizzabot.simulator      # local web chat → http://127.0.0.1:8765
```

---

## 4. هيكل الكود

```
pizzabot/
├── config.py               ROOT / DATA_DIR / QUARANTINE_DIR
├── flow/
│   ├── machine.py          ★ الـ state machine: handle(conversation, event, ctx) -> Result
│   ├── menu.py             Menu, Category, MenuItem, OptionGroup, Option, RestaurantSettings
│   ├── slots.py            مواعيد الفتح + pickup/delivery slots
│   └── demo.py             مطعم demo بمنيو صناعي
├── simulator.py            web chat محلي لتجربة الـ flow (stdlib بس، 127.0.0.1)
├── order/schema.py         Order, CartItem, CartOption, OrderStatus, Channel, Fulfilment
├── menu/normalizer.py      أعمدة Excel المنيو (it/en/ar) → dish_name, price_eur, category, available, ingredients
└── security/
    ├── upload/             فحص الملفات المرفوعة على 5 طبقات (policy, magic bytes, xlsx/pdf safety, quarantine)
    ├── tiers.py            access tiers   ⚠ فيه مشكلة، شوف §7
    ├── rate_limiter.py     sliding window لكل (tier, IP) + لكل sender
    ├── prompt_guard.py     ⚠ مش مستخدم (مفيش LLM)
    ├── pii_masker.py       إخفاء التليفونات والإيميلات
    └── audit.py            JSONL log → data/queries.jsonl
tests/                      test file لكل module (263 test)
docs/design-v1.md           الـ data model + الـ flow بالتفصيل (assumptions [A1]–[A10])
CONTEXT.md                  القرارات والـ gaps والخطة
```

### الـ state machine (`pizzabot/flow/machine.py`)

- **Pure:** مفيهاش I/O ولا clock (`ctx.now`) ولا randomness. الـ caller بيحمّل الـ conversation، ويكلّم `handle`، ويحفظ الناتج، ويعرض الـ screen، ويطبّق الـ effects.
- **Events:** `Choice(id)` لما الزبون يدوس زرار، و `Text(text)` لما يكتب.
- **الناتج:** `Result(conversation, screen, effects)`، حيث `Screen(text, options, accepts_text)` و `ScreenOption(id, label)`. الـ id قصير وASCII عشان يدخل في `callback_data` بتاع Telegram (64 byte).
- **Effect:** `PlaceOrder(order)` معناها احفظ الطلب وبلّغ المطبخ.
- **`FlowContext`:** `settings`, `menu`, `now`, `booked_slots`.
- **`Conversation`:** Pydantic بيتحفظ JSON، فيه `state` و `state_data` و `cart: list[CartLine]`. الـ `CartLine` فيه **ids بس** (`menu_item_id`, `option_ids`, `quantity`) من غير أسعار.
- الزراير القديمة (stale) بترجع رسالة "Questa scelta non è più valida." مع الشاشة الحالية.
- المنيو والحد الأدنى للطلب والـ slot بيتراجعوا تاني عند دخول الـ cart، وفي الملخص، وعند التأكيد.

**الـ states:**
`start → (closed) → category → item → item_detail → [option_group → quantity] → cart_review → edit_line → fulfilment → address (delivery بس) → slot → notes → confirm → done`

- كل شاشة ما عدا `start` فيها ⬅ Indietro و ✖ Annulla.
- **Quick add:** "➕ Aggiungi / ×2 / ×3" بيضيف النسخة الـ standard (أول option متاح في كل group مطلوب، من غير extras)، ويرجع لنفس قايمة الأصناف بـ "✓ Aggiunto: …". "⚙️ Personalizza" بيدخل على اختيار الـ options.
- شاشة الأصناف فيها 🛒 Carrello (العدد والإجمالي) و 📂 Altre categorie.
- الـ options الـ standard مابتظهرش في السطور اللي الزبون بيشوفها.
- **قاعدة السعر:** `unit price = base_price + Σ price_delta of chosen options`.
- الـ allergens هي الـ 14 بتوع EU (Reg. 1169/2011) كأرقام من 1 لـ 14، بتظهر من الـ DB بس، والـ extra ممكن يضيف allergens.

### الـ data model المخطط لـ PostgreSQL (من `docs/design-v1.md`)

```
restaurants, categories, menu_items, option_groups, options, item_option_groups
opening_hours, slot_settings
conversations   (restaurant_id, channel, customer_ref) unique; state, state_data jsonb, cart jsonb, expires_at (2h idle)
orders          public_code ('A-0427'), fulfilment, address, slot_at, kitchen_notes, totals, vat, status
order_lines, order_line_options   ← snapshots للأسماء والأسعار عشان الطلبات القديمة تفضل سليمة لو المنيو اتغير
```

الـ cart هيبقى في PostgreSQL مع `expires_at` مش في Redis (service أقل).

---

## 5. اللي خلص (git history)

1. Initial standalone project
2. Order schema: Decimal money، `restaurant_id`، تفصيل الـ IVA
3. إصلاح parsing الأسعار الإيطالية (`7,50`)، والـ false positives بتاعة Excel formula، و rate limit لكل sender
4. اعتماد الـ guided button flow وشيل الـ LLM من v1
5. `docs/design-v1.md`: الـ data model والـ flow
6. الـ state machine + توسيع `Order` للـ checkout (fulfilment, address, slot, notes, delivery fee)
7. الـ simulator المحلي + مطعم الـ demo
8. Quick add بالـ standard options والرجوع لقايمة الأصناف
9. زرار "Altre categorie" صريح
10. إخفاء الـ standard options من السطور اللي الزبون بيشوفها

---

## 6. الخطة الجاية (بالترتيب)

1. ✅ ~~State machine pure Python + tests~~
2. **⏳ Telegram + PostgreSQL end-to-end لمطعم واحد** ← **دي الخطوة الجاية**
   - Docker Compose على السيرفر (arm64): app + PostgreSQL
   - Schema + migrations للجداول اللي في §4
   - Storage layer: تحميل/حفظ `Conversation`، وحفظ `Order`، و `booked_slots`
   - Telegram adapter: Screen → inline keyboard، و callback → `Choice`، و message → `Text`، مع pagination
   - استيراد المنيو: Excel → `security/upload` → `menu/normalizer` → DB
   - إشعار المطبخ في Telegram group
   - Dependencies جديدة محتاجة تبرير: driver لـ PostgreSQL (`psycopg`) و Telegram client. **ممنوع LLM client.**
3. **التعميم:** auth لكل مطعم (بدل `X-Tier`)، audit، GDPR retention، وبعدين **WhatsApp** (Meta Cloud API).
4. بعد كده: تحديثات حالة الطلب للزبون، و e-invoicing الإيطالي (بس لو المحاسب قال لازم).

**بالتوازي (مش كود):**
- منيوهات حقيقية من 2–3 بيتزاريات، تثبت أو تنفي assumptions [A1]–[A10]
- البدء بدري في WhatsApp Business verification
- محاسب يحدد نسبة الـ IVA، وهل البوت بياخد الطلبات بس والمطعم بيطلع الإيصال من الكاشير بتاعه

---

## 7. مشاكل معروفة (لازم تتحل قبل الـ production)

| المكان | المشكلة |
|---|---|
| `tiers.py` + `rate_limiter.py` | الـ tier جاي من header `X-Tier` اللي الـ client بيبعته، فأي حد يقدر يدّعي إنه `agent`. والأسماء لسه بتاعة العقارات (`anonymous/lead/agent`). لازم تتغير لـ roles حقيقية (`customer / restaurant / admin`). الـ webhooks تستخدم `allow_sender(restaurant_id, sender_id)`. |
| `prompt_guard.py` | مالوش لازمة من غير LLM. يتشال من الـ request path أو يتحذف. |
| `audit.py` | بيخزن preview للنص الخام 120 حرف (PII)، ومفيهوش `restaurant_id`، والـ fields بتاعة RAG (`question`, `sources_count`). |
| `pii_masker.py` | الـ regex بتاع التليفون بيخفي أرقام الطلبات كمان (`20261015`)، والـ keys بتاعة العقارات (`agent_phone`). |
| `security/upload/*` | رسايل الخطأ بالعربي، والمشغّلين إيطاليين. |
| GDPR | التليفونات والعناوين وملاحظات المطبخ PII، ولسه محتاجين نحدد سياسة الاحتفاظ بالبيانات. |
| Fiscal | نسبة الـ IVA ونطاق الإيصال، والاتنين محتاجين محاسب. |

### Assumptions مفتوحة (من `docs/design-v1.md`)

- [A1] من 20 لـ 60 صنف، ومن 4 لـ 8 categories
- [A2] الحجم والعجينة بيغيروا السعر بفرق ثابت
- [A3] الـ extras سعرها ثابت مهما كان الحجم
- [A4] الحاجات المتشالة ("senza …") ببلاش وبتروح في الملاحظات
- [A5] مفيش metà e metà ولا pizza al metro
- [A6] الدفع عند الاستلام، ومفيش online payment
- [A7] إيطالي بس
- [A8] الـ allergens بيدخلها المشغّل
- [A9] نفس اليوم بس
- [A10] الـ delivery zones بالـ CAP

أسئلة لسه مفتوحة: شكل الـ delivery zones، اسم الزبون (نسأله ولا ناخده من الـ profile)، البيرة في الـ delivery، والدفع.

---

## 8. تعليمات لـ Claude chat وهو بيكتب الـ prompts

- الـ prompts هتتنفذ بـ **Claude Code** على السيرفر جوه `~/PizzaBot`، وClaude Code ده عنده access للكود والـ terminal و git.
- كل prompt يغطي **خطوة واحدة صغيرة** وفيها تعريف واضح لـ "done"، زي tests بتعدّي أو أمر معيّن شغال.
- لازم يحترم القرارات في §2: مفيش LLM، والأسعار من الـ DB، و `restaurant_id` في كل حتة، و `Decimal`.
- الـ state machine تفضل **pure**. الـ I/O (DB و Telegram) يبقى في layers بره `pizzabot/flow/`.
- كل خطوة فيها tests، وكل الـ tests تعدّي قبل الـ commit، و commit message واضح.
- الـ Docker images لازم تدعم **arm64**، وماتلمسش المشاريع التانية اللي على السيرفر.
- الـ secrets (Telegram token وباسورد الـ DB) تبقى في `.env` (موجود في `.gitignore`)، ومايتعملهاش commit أبداً.
- أي dependency جديدة تتبرر وتتعمل pinned في `requirements.txt`.
- نص الزبون بالإيطالي، والكود والـ comments بالإنجليزي.

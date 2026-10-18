# PizzaBot — ملخص المشروع (للاستخدام في Claude chat)

> آخر تحديث: 2026-10-18 · آخر commit: Telegram adapter + إشعار المطبخ (بعد `c1be304`)
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
   - `vat_rate` **لكل مطعم في الـ DB ومن غير default** (`restaurants.vat_rate NOT NULL`)، والـ storage بيرفض أي مطعم من غيره (`ConfigError`) ومابيرجعش أبداً للـ 22% بتاعة Python. كل order بياخد نسخة منه.
   - مطعم الـ demo متسجّل بـ **10%** (`DEMO_VAT_RATE` في `seed_demo.py`)، و**لسه مستني تأكيد المحاسب** (10% للأكل takeaway/delivery ولا 22%).
3. **Multi-tenant من أول يوم.** كل صنف وcart وorder وlog فيه `restaurant_id`، ومفيش shared global state.
4. **Authentication حقيقي.** كل مطعم بـ API key/token، والزبون بيتعرّف بالـ channel + رقم التليفون/chat id.

---

## 3. البيئة

| | |
|---|---|
| اللغة | Python 3.14، Pydantic v2 |
| Dependencies | pydantic, fastapi, pandas, openpyxl, puremagic, pypdf, pytest, `psycopg[binary]==3.3.6` (كلها pinned في `requirements.txt`). الـ Telegram client معمول بـ stdlib (urllib + json)، مفيش dependency جديدة |
| Repo | https://github.com/mohamdhamed/PizzaBot (branch `main`) |
| جهاز التطوير | Fedora، `/mnt/windows/PizzaBot`، ومفيش Docker/PostgreSQL عليه |
| السيرفر | Oracle Cloud، Ubuntu، **aarch64 (ARM)**، 23GB RAM، الـ SSH alias `pulsar` (user `ubuntu`) |
| المشروع على السيرفر | `~/PizzaBot` مع `.venv`. الـ tests: **411 بيعدّوا في الـ container** (من غير skip)، وعلى الـ host 342 بيعدّوا و 69 skipped (tests الـ DB محتاجة `DATABASE_URL`) |
| Docker | compose project `pizzabot`: `db` (postgres:17، volume `pizzabot_pgdata`، network `pizzabot_net`، من غير ports) و `app` (python:3.14-slim، user مش root، `restart: unless-stopped`، من غير ports). الـ `app` بيعمل migrate وبعدين long polling لـ Telegram |
| البوت | `@Pizzabotit_bot`، بيخدم مطعم واحد (`RESTAURANT_ID=demo`) |
| Claude Code على السيرفر | متسطب (`~/.local/bin/claude`)، وبيشتغل جوه `tmux` بـ `--remote-control` عشان أوصله من الموبايل |
| الـ Deployment | كل حاجة في **Docker** على السيرفر، و PostgreSQL جواه (images لازم تدعم arm64) |

على السيرفر برضه في مشاريع تانية (`BotBudget`, `openclaw`, `postgres`)، **ماتتلمسش**.

الأوامر:

```bash
docker compose up -d --build                                   # db + البوت
docker compose logs -f app                                     # logs (من غير نصوص الزباين)
docker compose run --rm app python -m pytest tests -q          # كل الـ tests، مع PostgreSQL
docker compose run --rm app python -m pizzabot.db.seed_demo    # مطعم الـ demo
docker compose run --rm app python -m pizzabot.db.set_kitchen_chat demo <chat_id>
.venv/bin/python -m pytest tests/ -q                           # على الـ host (tests الـ DB بتتعمل skip)
.venv/bin/python -m pizzabot.simulator                         # web chat محلي → http://127.0.0.1:8765
```

الـ secrets (`POSTGRES_PASSWORD`, `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`) في `.env` بس (mode 600، في `.gitignore`)، والقالب `.env.example`.

---

## 4. هيكل الكود

```
migrations/                 0001_init.sql (الـ schema)، 0002_kitchen_notify_asap.sql
pizzabot/
├── config.py               ROOT / DATA_DIR / QUARANTINE_DIR
├── service.py              ★ process_event: حدث واحد في transaction واحدة (lock → handle → save_order → save)
├── storage/                psycopg من غير ORM: menu/settings، conversations (FOR UPDATE + expiry 2h)، orders (snapshot، booked_slots، إشعار المطبخ)
├── db/                     migrate (advisory lock + checksums)، ping، seed_demo، set_kitchen_chat
├── channels/telegram/      ★ api (urllib، retry/backoff/429)، render (keyboard + pagination)، bot (long polling)، kitchen (رسالة المطبخ + retry)، find_chats
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
tests/                      test file لكل module؛ tests/db/ ضد PostgreSQL حقيقي (DB مؤقتة بتتمسح)
docs/design-v1.md           الـ data model + الـ flow بالتفصيل (assumptions [A1]–[A10])
CONTEXT.md                  القرارات والـ gaps والخطة
```

### الـ state machine (`pizzabot/flow/machine.py`)

- **Pure:** مفيهاش I/O ولا clock (`ctx.now`) ولا randomness. الـ caller بيحمّل الـ conversation، ويكلّم `handle`، ويحفظ الناتج، ويعرض الـ screen، ويطبّق الـ effects.
- **Events:** `Choice(id)` لما الزبون يدوس زرار، و `Text(text)` لما يكتب، و `Other()` لأي حاجة تانية (صورة، sticker، voice، location…): بيرد "Usa i pulsanti" ومابيتاخدش أبداً كعنوان أو ملاحظة.
- **الناتج:** `Result(conversation, screen, effects)`، حيث `Screen(text, options, accepts_text)` و `ScreenOption(id, label)`. الـ id قصير وASCII عشان يدخل في `callback_data` بتاع Telegram (64 byte).
- **Effect:** `PlaceOrder(order)` معناها احفظ الطلب وبلّغ المطبخ. الـ DB هي اللي بتدّي رقم الطلب (`public_code`)، والـ service بتسجله بـ `record_order_code` وتعيد `render`، فشاشة `done` بتقول "Ordine n. 001 inviato! ✅". الـ machine لسه pure.
- **"Prima possibile" (decision A):** عند التأكيد بياخد **أقرب slot لسه فيه مكان** (من `ctx.now` و `ctx.booked_slots`)، وبيتحفظ في `slot_at` مع `asap=true`، فبيتحسب في سعة الـ slot زي أي طلب. لو مفيش ولا slot فاضل النهارده: رسالة قصيرة ويرجع لاختيار الـ slot، ومفيش طلب بيتعمل. ومن غير slots فاضلة النهارده الزبون مايقدرش يبدأ طلب أصلاً (`closed`).
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

### الـ data model في PostgreSQL (`migrations/`)

```
restaurants, categories, menu_items, option_groups, options, item_option_groups
opening_hours, slot_settings
conversations   (restaurant_id, channel, customer_ref) unique; state, state_data jsonb, cart jsonb, expires_at (2h idle)
order_counters  (restaurant_id, business_date) → last_seq
orders          public_code ('001'، لكل مطعم في كل يوم عمل)، fulfilment، address، slot_at، asap، kitchen_notes،
                totals، vat_rate/vat_eur/net_eur، status، kitchen_notified_at
order_lines, order_line_options   ← snapshots للأسماء والأسعار عشان الطلبات القديمة تفضل سليمة لو المنيو اتغير
```

- Ids قصيرة ASCII (لحد 48 حرف)، والـ primary key `(restaurant_id, id)`، والـ foreign keys كلها composite بالـ `restaurant_id`، فمفيش row يقدر يشاور على داتا مطعم تاني.
- الفلوس `NUMERIC(10,2)` و `Decimal` في Python. فيه CHECKs: `items + fee = total` و `net + vat = total`.
- `public_code` بيتعمل بـ trigger (`BEFORE INSERT`) من `order_counters` حسب timezone المطعم.
- الـ cart في PostgreSQL مع `expires_at`، مش Redis.

### Double confirm والتزامن

- **نفس الزبون:** `load_conversation(..., for_update=True)` بيقفل الـ row بتاعه (وبيعمل row منتهي الأول لو مش موجود، عشان يبقى فيه حاجة تتقفل). الـ "Conferma" التانية بتستنى، وبعدين تلاقي `state=done` فترجع `MSG_STALE` ومن غير طلب تاني.
- **زباين مختلفين على نفس الـ slot:** قبل التأكيد `pg_advisory_xact_lock(0x505A4F52, hashtext(restaurant_id))`، فالتأكيدات بتمشي واحد ورا التاني لكل مطعم، وحساب السعة دايماً بيشوف الحجوزات اللي اتعملت commit. ده بيغطي "prima possibile" كمان (test: طلبين asap على آخر slot → طلب واحد بس).
- الـ session بتاعة البوت فيها `lock_timeout=2s` و `statement_timeout=5s`، ولو حصل timeout الزبون بيشوف "Riprova tra un attimo 🙏" والـ loop بيكمل.
- اتعمل mutation test: شيل أي lock من الاتنين بيوقّع test.

### الـ Telegram adapter (`pizzabot/channels/telegram/`)

- **Long polling** (`getUpdates` بـ offset)، مطعم واحد من `RESTAURANT_ID`. لو ناقص أو مش موجود في الـ DB، أو الـ token مرفوض، بيخرج فوراً (exit 2).
- **Private chats بس.** الـ groups كلها بتتجاهل، وجروب المطبخ بيستقبل الإشعارات بس. `customer_ref = from.id`.
- `callback_query` → `Choice(data)`، بعد التأكد إن الـ data ≤ 64 byte. **`answerCallbackQuery` بيتبعت دايماً** (حتى مع الأخطاء). الرسالة اللي اتداس عليها بتتعدل (`editMessageText`)، ولو التعديل فشل بتتبعت رسالة جديدة.
- `/start` → شاشة البداية بـ conversation جديدة (`process_event(..., restart=True)` بيمسح الـ cart، زي Annulla). أي `/command` تاني أو رسالة مش نص → `Other`.
- **Pagination** (أكتر من 8 options): زراير `~pg:<n>`، والـ machine ids مابتبدأش أبداً بـ `~` (الـ renderer بيتأكد). تقليب الصفحة مابيعدّيش على الـ machine: البوت شايل آخر screen اتبعتت لكل chat في الذاكرة وبيعيد رسمها. لو الذاكرة مش فيها الرسالة دي (restart أو رسالة قديمة)، الزرار بيروح للـ machine فترد "scelta non più valida" مع الشاشة الحالية.
- **Logs:** update ids، الـ restaurant id، نوع الحدث، أرقام الطلبات، وأنواع الأخطاء بس. مفيش نص رسايل ولا عناوين ولا تليفونات ولا ملاحظات ولا user ids. الـ token مابيظهرش في exceptions ولا logs (وفيه filter بيعمل redact كخط دفاع أخير).

### إشعار المطبخ

- بعد الـ commit بتتبعت رسالة للـ `restaurants.kitchen_telegram_chat_id`، مبنية من الـ DB (الـ snapshots) بالإيطالي: الرقم، Ritiro/Consegna + الميعاد (أو "Prima possibile (entro HH:MM)")، السطور بالـ options والكميات، "⚠️ NOTE PER LA CUCINA"، والعنوان للـ delivery، والإجمالي.
- `kitchen_notified_at` بيتكتب بس بعد ما Telegram يأكد. لو فشل، الطلب مابيتأثرش وبيفضل pending، والـ polling loop بيعيد المحاولة بـ backoff (5s → 10s → … لحد 5 دقايق). ده at-least-once: ممكن الرسالة توصل مرتين في حالة نادرة، لكن مستحيل ماتوصلش.
- لو `kitchen_telegram_chat_id` فاضي: warning واضح فيه الأمر اللي يتشغّل، والطلبات بتفضل pending. لو الجروب اتحوّل لـ supergroup، الـ id الجديد بيتحفظ لوحده.
- طلبات الأيام اللي فاتت مابتتبعتش (طلبات نفس اليوم بس، [A9])، وبيطلع عنها warning.

---

## 5. اللي خلص (git history)

0. Initial standalone project
2. Order schema: Decimal money، `restaurant_id`، تفصيل الـ IVA
3. إصلاح parsing الأسعار الإيطالية (`7,50`)، والـ false positives بتاعة Excel formula، و rate limit لكل sender
4. اعتماد الـ guided button flow وشيل الـ LLM من v1
5. `docs/design-v1.md`: الـ data model والـ flow
6. الـ state machine + توسيع `Order` للـ checkout (fulfilment, address, slot, notes, delivery fee)
7. الـ simulator المحلي + مطعم الـ demo
8. Quick add بالـ standard options والرجوع لقايمة الأصناف
9. زرار "Altre categorie" صريح
10. إخفاء الـ standard options من السطور اللي الزبون بيشوفها (`d5fa2c5`)
11. Docker Compose: app + PostgreSQL 17 (`394d1ce`)
12. Schema أولي + migration runner بسيط (`deb9c2c`)
13. `PROJECT_SUMMARY.md` (`6795321`)
14. Storage layer + `process_event` في transaction واحدة، seed_demo، double-confirm locks (`c1be304`)
15. **Telegram adapter** (long polling، مطعم واحد) + **إشعار المطبخ** + رقم الطلب في شاشة done + prima possibile بياخد slot (migration 0002)

تجربة جانبية (branch `experiment/local-llm`، `b7a81f2`، **مش متعملها merge**): Ollama + qwen2.5 3B/7B على CPU السيرفر بيحوّلوا نص إيطالي حر لـ ids المنيو. النتيجة: الـ 7B مظبوط 15/20 لكن بياخد ~13 ثانية للرسالة و ~3.5 دقيقة لما 4 يكتبوا مع بعض، والـ 3B بيغلط كتير (6/20). **القرار #1 (مفيش LLM) فضل زي ما هو.**

---

## 6. الخطة الجاية (بالترتيب)

1. ✅ ~~State machine pure Python + tests~~
2. **⏳ Telegram + PostgreSQL end-to-end لمطعم واحد**
   - ✅ Docker Compose على السيرفر (arm64): app + PostgreSQL
   - ✅ Schema + migrations
   - ✅ Storage layer: `Conversation` و `Order` و `booked_slots`
   - ✅ Telegram adapter: inline keyboard، callbacks، pagination، long polling
   - ✅ إشعار المطبخ في Telegram group
   - ⏳ **تجربة يدوية ببوت حقيقي وجروب مطبخ حقيقي** ← دي الجاية
   - ⏳ استيراد المنيو: Excel → `security/upload` → `menu/normalizer` → DB
   - الـ Telegram client اتعمل بـ stdlib، فمفيش dependency جديدة. **ممنوع LLM client.**
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
| GDPR | العناوين وملاحظات المطبخ والـ Telegram ids كلها PII، ولسه محتاجين نحدد سياسة الاحتفاظ بالبيانات (الـ conversations المنتهية والـ orders القديمة لسه مابتتمسحش). |
| Fiscal | نسبة الـ IVA ونطاق الإيصال، والاتنين محتاجين محاسب. الـ demo على 10%. |
| رقم تليفون الزبون | الـ flow مابيسألش عن التليفون، فرسالة المطبخ للـ delivery فيها link للـ Telegram بتاع الزبون بداله. لو المطبخ محتاج يتصل: خطوة `request_contact` في الـ flow. |
| Pagination | آخر screen لكل chat متخزنة في الذاكرة. بعد restart، زراير الصفحات في الرسايل القديمة بتعرض الشاشة الحالية مع "scelta non più valida". ده مقبول مع instance واحدة، لكن مع webhook أو أكتر من instance لازم تتخزن في الـ DB. |
| Replay بعد crash | لو الـ process وقع وسط batch، الـ updates اللي ماتأكدتش بتتبعت تاني. الـ machine بترد "stale" على الزراير المتكررة (ومفيش طلب مكرر)، لكن نص اتكتب ممكن يترد عليه مرتين. |
| إشعار المطبخ | at-least-once: لو Telegram أكد والـ process وقع قبل الـ UPDATE، الرسالة بتتبعت تاني. والـ backoff في الذاكرة، فبيبدأ من الأول بعد أي restart. |
| Lock timeout | 2s للـ lock و 5s للـ statement، لسه ماتجربوش تحت ضغط حقيقي. |

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

أسئلة لسه مفتوحة:
- نسبة الـ IVA (10% ولا 22%)، ونطاق الإيصال (المحاسب).
- رقم التليفون للـ delivery: نطلبه في الـ flow ولا الـ link للـ Telegram كفاية؟
- اسم الزبون: نسأله ولا ناخده من الـ profile؟
- شكل الـ delivery zones، والبيرة في الـ delivery، والدفع.
- "Prima possibile" بياخد أقرب slot بعد `min_lead_minutes`: هل ده الوعد الصح للزبون (مثلاً "entro 20:30")؟
- طلبات الأيام اللي فاتت اللي ماوصلتش للمطبخ: تتبعت، ولا تتسجل بس (الحالي: بتتسجل بس)؟

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

-- 0001_init.sql — initial schema (docs/design-v1.md §2, pizzabot/order/schema.py,
-- pizzabot/flow/menu.py).
--
-- Conventions
-- * Ids are short ASCII text, as in the Python models: menu ids end up in
--   button callback data ("opt:<id>", 64-byte Telegram limit), so they are
--   capped at 48 chars. Menu ids are scoped per restaurant: the primary key
--   is (restaurant_id, id), so two restaurants can both have "margherita".
-- * Multi-tenancy: every tenant table has restaurant_id NOT NULL, and children
--   reference parents with composite (restaurant_id, parent_id) foreign keys,
--   so a row can never point at another restaurant's data.
-- * Money is NUMERIC(10,2), IVA-inclusive. vat_rate is a fraction (0.22), as in
--   the Python code.
-- * Orders are an immutable snapshot: names and prices are copied into
--   order_lines / order_line_options and do not reference the menu.

-- --- domains ------------------------------------------------------------------

CREATE DOMAIN short_id AS text
    CHECK (VALUE ~ '^[A-Za-z0-9_-]{1,48}$');

-- Non-negative euro amount (prices, fees, totals).
CREATE DOMAIN eur AS numeric(10,2)
    CHECK (VALUE >= 0);

-- IVA as a fraction: 0.22 = 22 %.
CREATE DOMAIN vat_rate AS numeric(5,2)
    CHECK (VALUE >= 0 AND VALUE < 1);

-- EU Reg. 1169/2011 Annex II allergens, numbered 1–14.
CREATE DOMAIN allergen_set AS smallint[]
    CHECK (VALUE <@ ARRAY[1,2,3,4,5,6,7,8,9,10,11,12,13,14]::smallint[]);

-- --- restaurants & settings ---------------------------------------------------

CREATE TABLE restaurants (
    id                       short_id    PRIMARY KEY,
    name                     text        NOT NULL CHECK (btrim(name) <> ''),
    timezone                 text        NOT NULL DEFAULT 'Europe/Rome',
    -- Default IVA for new orders. No default here: the rate must be chosen per
    -- restaurant (10 % vs 22 % is still open with the accountant).
    vat_rate                 vat_rate    NOT NULL,
    pickup_enabled           boolean     NOT NULL DEFAULT true,
    delivery_enabled         boolean     NOT NULL DEFAULT true,
    delivery_fee_eur         eur         NOT NULL DEFAULT 0,
    min_order_eur            eur         NOT NULL DEFAULT 0,
    -- Empty = deliver anywhere; otherwise the CAP list (5 digits each).
    delivery_postcodes       text[]      NOT NULL DEFAULT '{}'
        CHECK (array_to_string(delivery_postcodes, ',') ~ '^([0-9]{5}(,[0-9]{5})*)?$'),
    -- Telegram group/chat that receives new orders (negative for groups).
    kitchen_telegram_chat_id bigint,
    created_at               timestamptz NOT NULL DEFAULT now()
);

-- Several ranges per day are allowed (lunch + dinner). weekday 0 = Monday,
-- as datetime.weekday(). Ranges crossing midnight are not supported in v1.
CREATE TABLE opening_hours (
    id            bigint   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    restaurant_id short_id NOT NULL REFERENCES restaurants (id) ON DELETE CASCADE,
    weekday       smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    opens_at      time     NOT NULL,
    closes_at     time     NOT NULL,
    CHECK (opens_at < closes_at)
);
CREATE INDEX opening_hours_restaurant_idx ON opening_hours (restaurant_id, weekday);

CREATE TABLE slot_settings (
    restaurant_id       short_id PRIMARY KEY REFERENCES restaurants (id) ON DELETE CASCADE,
    slot_minutes        integer  NOT NULL DEFAULT 15 CHECK (slot_minutes BETWEEN 5 AND 240),
    max_orders_per_slot integer  NOT NULL DEFAULT 4  CHECK (max_orders_per_slot > 0),
    min_lead_minutes    integer  NOT NULL DEFAULT 20 CHECK (min_lead_minutes >= 0)
);

-- --- menu -----------------------------------------------------------------------

CREATE TABLE categories (
    restaurant_id short_id NOT NULL REFERENCES restaurants (id) ON DELETE CASCADE,
    id            short_id NOT NULL,
    name          text     NOT NULL CHECK (btrim(name) <> ''),
    position      integer  NOT NULL DEFAULT 0,
    active        boolean  NOT NULL DEFAULT true,
    PRIMARY KEY (restaurant_id, id)
);

CREATE TABLE menu_items (
    restaurant_id  short_id     NOT NULL,
    id             short_id     NOT NULL,
    category_id    short_id     NOT NULL,
    name           text         NOT NULL CHECK (btrim(name) <> ''),
    description    text         NOT NULL DEFAULT '',
    base_price_eur eur          NOT NULL,
    allergens      allergen_set NOT NULL DEFAULT '{}',
    available      boolean      NOT NULL DEFAULT true,
    position       integer      NOT NULL DEFAULT 0,
    age_restricted boolean      NOT NULL DEFAULT false,
    PRIMARY KEY (restaurant_id, id),
    FOREIGN KEY (restaurant_id, category_id) REFERENCES categories (restaurant_id, id)
);

-- Dimensione 1/1, Impasto 1/1, Aggiunte 0/N.
CREATE TABLE option_groups (
    restaurant_id short_id NOT NULL REFERENCES restaurants (id) ON DELETE CASCADE,
    id            short_id NOT NULL,
    name          text     NOT NULL CHECK (btrim(name) <> ''),
    position      integer  NOT NULL DEFAULT 0,
    min_select    integer  NOT NULL DEFAULT 1,
    max_select    integer  NOT NULL DEFAULT 1,
    PRIMARY KEY (restaurant_id, id),
    CHECK (min_select >= 0 AND max_select >= 1 AND min_select <= max_select)
);

-- Option ids are unique per restaurant across groups (Menu.option() looks them
-- up without the group). The standard option of a required group is the first
-- by position — there is no is_default flag (pizzabot/flow/machine.py).
CREATE TABLE options (
    restaurant_id   short_id      NOT NULL,
    id              short_id      NOT NULL,
    option_group_id short_id      NOT NULL,
    name            text          NOT NULL CHECK (btrim(name) <> ''),
    -- Signed: Baby is −1.00. A line's unit price is checked >= 0 on order_lines.
    price_delta_eur numeric(10,2) NOT NULL DEFAULT 0,
    allergens       allergen_set  NOT NULL DEFAULT '{}',
    available       boolean       NOT NULL DEFAULT true,
    position        integer       NOT NULL DEFAULT 0,
    PRIMARY KEY (restaurant_id, id),
    FOREIGN KEY (restaurant_id, option_group_id)
        REFERENCES option_groups (restaurant_id, id) ON DELETE CASCADE
);

-- Which groups an item offers, in display order (MenuItem.option_group_ids).
CREATE TABLE item_option_groups (
    restaurant_id   short_id NOT NULL,
    menu_item_id    short_id NOT NULL,
    option_group_id short_id NOT NULL,
    position        integer  NOT NULL DEFAULT 0,
    PRIMARY KEY (restaurant_id, menu_item_id, option_group_id),
    FOREIGN KEY (restaurant_id, menu_item_id)
        REFERENCES menu_items (restaurant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (restaurant_id, option_group_id)
        REFERENCES option_groups (restaurant_id, id) ON DELETE CASCADE
);

-- --- conversations (guided-flow state + cart) ----------------------------------

-- One row per (restaurant, channel, customer). state_data = Conversation.data;
-- cart holds ids only (CartLine), never prices.
CREATE TABLE conversations (
    id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    restaurant_id short_id    NOT NULL REFERENCES restaurants (id) ON DELETE CASCADE,
    channel       text        NOT NULL CHECK (channel IN ('telegram', 'whatsapp')),
    customer_ref  text        NOT NULL CHECK (btrim(customer_ref) <> ''),
    state         text        NOT NULL DEFAULT 'start' CHECK (state <> ''),
    state_data    jsonb       NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(state_data) = 'object'),
    cart          jsonb       NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(cart) = 'array'),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    CONSTRAINT conversations_key UNIQUE (restaurant_id, channel, customer_ref)
);
CREATE INDEX conversations_expires_idx ON conversations (expires_at);

-- --- orders ------------------------------------------------------------------------

-- public_code: short code for kitchen and customer, unique per restaurant per
-- business day (the calendar date of created_at in the restaurant's timezone).
-- The insert trigger below sets business_date and, when public_code is NULL,
-- allocates the next number of the day from order_counters ("001", "002", …).
CREATE TABLE order_counters (
    restaurant_id short_id NOT NULL REFERENCES restaurants (id) ON DELETE CASCADE,
    business_date date     NOT NULL,
    last_seq      integer  NOT NULL CHECK (last_seq > 0),
    PRIMARY KEY (restaurant_id, business_date)
);

CREATE TABLE orders (
    order_id         short_id    PRIMARY KEY,           -- Order.order_id (uuid4 hex)
    restaurant_id    short_id    NOT NULL REFERENCES restaurants (id),
    public_code      text        NOT NULL CHECK (public_code ~ '^[A-Z0-9-]{1,12}$'),
    business_date    date        NOT NULL,              -- set by trigger
    channel          text        NOT NULL CHECK (channel IN ('telegram', 'whatsapp')),
    customer_ref     text        NOT NULL CHECK (btrim(customer_ref) <> ''),
    fulfilment       text        NOT NULL DEFAULT 'pickup'
        CHECK (fulfilment IN ('pickup', 'delivery')),
    address          text,
    slot_at          timestamptz,                       -- NULL = "prima possibile"
    -- Verbatim, never parsed. Control characters other than newline are
    -- rejected (clean_kitchen_notes strips them); blank notes are NULL.
    kitchen_notes    text
        CHECK (kitchen_notes IS NULL OR (
               char_length(kitchen_notes) BETWEEN 1 AND 300
               AND kitchen_notes !~ '[\x01-\x09\x0b-\x1f\x7f]')),
    items_total_eur  eur         NOT NULL,
    delivery_fee_eur eur         NOT NULL DEFAULT 0,
    total_eur        eur         NOT NULL,
    vat_rate         vat_rate    NOT NULL,              -- NULL on insert → restaurant default
    vat_eur          eur         NOT NULL,
    net_eur          eur         NOT NULL,
    status           text        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'in_kitchen', 'ready', 'delivered', 'cancelled')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT orders_tenant_key UNIQUE (restaurant_id, order_id),
    CONSTRAINT orders_public_code_key UNIQUE (restaurant_id, business_date, public_code),
    CONSTRAINT orders_total_check CHECK (items_total_eur + delivery_fee_eur = total_eur),
    CONSTRAINT orders_vat_split_check CHECK (net_eur + vat_eur = total_eur),
    CONSTRAINT orders_delivery_address_check CHECK (fulfilment <> 'delivery' OR address IS NOT NULL),
    CONSTRAINT orders_pickup_fee_check CHECK (fulfilment <> 'pickup' OR delivery_fee_eur = 0)
);
CREATE INDEX orders_slot_idx ON orders (restaurant_id, slot_at);

CREATE FUNCTION orders_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    tz   text;
    rate numeric;
    seq  integer;
BEGIN
    SELECT r.timezone, r.vat_rate INTO tz, rate
      FROM restaurants r WHERE r.id = NEW.restaurant_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown restaurant %', NEW.restaurant_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    NEW.business_date := (NEW.created_at AT TIME ZONE tz)::date;

    IF NEW.vat_rate IS NULL THEN
        NEW.vat_rate := rate;
    END IF;

    IF NEW.public_code IS NULL THEN
        INSERT INTO order_counters AS c (restaurant_id, business_date, last_seq)
             VALUES (NEW.restaurant_id, NEW.business_date, 1)
        ON CONFLICT (restaurant_id, business_date)
             DO UPDATE SET last_seq = c.last_seq + 1
        RETURNING c.last_seq INTO seq;
        NEW.public_code := lpad(seq::text, 3, '0');
    END IF;

    RETURN NEW;
END
$$;

CREATE TRIGGER orders_before_insert
    BEFORE INSERT ON orders
    FOR EACH ROW EXECUTE FUNCTION orders_before_insert();

-- Snapshot of CartItem. menu_item_id is kept for reporting but is not a
-- foreign key: the order must survive the item being edited or deleted.
CREATE TABLE order_lines (
    id             bigint   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    restaurant_id  short_id NOT NULL,
    order_id       short_id NOT NULL,
    position       smallint NOT NULL CHECK (position >= 0),
    menu_item_id   short_id NOT NULL,
    dish_name      text     NOT NULL CHECK (btrim(dish_name) <> ''),
    quantity       integer  NOT NULL CHECK (quantity > 0),
    unit_price_eur eur      NOT NULL,
    subtotal_eur   eur      NOT NULL,
    CONSTRAINT order_lines_tenant_key UNIQUE (restaurant_id, id),
    UNIQUE (order_id, position),
    CHECK (subtotal_eur = unit_price_eur * quantity),
    FOREIGN KEY (restaurant_id, order_id)
        REFERENCES orders (restaurant_id, order_id) ON DELETE CASCADE
);

-- Snapshot of CartOption.
CREATE TABLE order_line_options (
    restaurant_id   short_id      NOT NULL,
    order_line_id   bigint        NOT NULL,
    position        smallint      NOT NULL CHECK (position >= 0),
    option_id       short_id      NOT NULL,
    name            text          NOT NULL CHECK (btrim(name) <> ''),
    price_delta_eur numeric(10,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (order_line_id, position),
    FOREIGN KEY (restaurant_id, order_line_id)
        REFERENCES order_lines (restaurant_id, id) ON DELETE CASCADE
);

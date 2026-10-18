-- 0002_kitchen_notify_asap.sql — kitchen notification state + "prima possibile"
-- orders that hold a slot.
--
-- * kitchen_notified_at: set only after Telegram confirmed the kitchen message.
--   NULL = still to send; the bot retries these (partial index below).
-- * asap: the customer chose "prima possibile". The order still gets slot_at,
--   the nearest slot with capacity at confirm, so it counts against
--   max_orders_per_slot like any other order. Orders from before this rule
--   have asap = false and may have slot_at NULL.

ALTER TABLE orders
    ADD COLUMN kitchen_notified_at timestamptz,
    ADD COLUMN asap boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT orders_asap_slot_check CHECK (NOT asap OR slot_at IS NOT NULL);

CREATE INDEX orders_kitchen_pending_idx ON orders (restaurant_id, created_at)
    WHERE kitchen_notified_at IS NULL;

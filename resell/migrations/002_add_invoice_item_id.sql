-- 002_add_invoice_item_id.sql
-- Add invoice_item_id to orders table for SellAuth replace-delivered endpoint.

ALTER TABLE orders ADD COLUMN invoice_item_id INTEGER;

-- Backfill from item_id for existing orders
UPDATE orders SET invoice_item_id = item_id WHERE invoice_item_id IS NULL;

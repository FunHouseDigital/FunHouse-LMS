-- Approved Holiday Special selling price: R250.00 (25,000 cents).
--
-- Existing payments retain their captured amount_cents snapshots, and existing
-- entitlements retain the same product row because this updates only the
-- catalog price in place. The predicate makes migration replay a no-op once
-- the approved price is present.

UPDATE products
SET price_cents = 25000
WHERE name = 'Holiday Special'
  AND price_cents IS DISTINCT FROM 25000;

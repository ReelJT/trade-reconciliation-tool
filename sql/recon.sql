-- Trade reconciliation in SQL (SQLite). Run with: python src/run_sql.py
-- Tables: internal_trades, broker_confirms, start_positions, custodian_positions

-- 1. Duplicate confirms: the broker sent the same trade more than once
DROP VIEW IF EXISTS broker_dedup;
CREATE VIEW broker_dedup AS
SELECT rowid AS rid, * FROM broker_confirms
WHERE rowid IN (
    SELECT MIN(rowid) FROM broker_confirms
    GROUP BY client_ref, trade_date, symbol, side, quantity, price
);

-- 2. Pass 1: match on our client reference
DROP VIEW IF EXISTS matched_ref;
CREATE VIEW matched_ref AS
SELECT i.trade_id, b.broker_ref, 'reference' AS match_method
FROM internal_trades i
JOIN broker_dedup b ON b.client_ref = i.client_ref;

-- 3. Pass 2: match leftovers on trade details, price within $0.005
DROP VIEW IF EXISTS matched_details;
CREATE VIEW matched_details AS
SELECT i.trade_id, b.broker_ref, 'details' AS match_method
FROM internal_trades i
JOIN broker_dedup b
  ON b.trade_date = i.trade_date AND b.symbol = i.symbol
 AND b.side = i.side AND b.quantity = i.quantity
 AND ABS(b.price - i.price) <= 0.005
WHERE i.trade_id NOT IN (SELECT trade_id FROM matched_ref)
  AND b.broker_ref NOT IN (SELECT broker_ref FROM matched_ref);

DROP VIEW IF EXISTS matched;
CREATE VIEW matched AS
SELECT * FROM matched_ref UNION ALL SELECT * FROM matched_details;

-- 4. Field by field breaks on matched pairs
-- name: trade_breaks
SELECT m.trade_id, m.broker_ref, i.symbol,
       TRIM(
         CASE WHEN i.side <> b.side THEN 'SIDE ' ELSE '' END ||
         CASE WHEN i.quantity <> b.quantity THEN 'QUANTITY ' ELSE '' END ||
         CASE WHEN ABS(i.price - b.price) > 0.005 THEN 'PRICE ' ELSE '' END ||
         CASE WHEN i.settle_date <> b.settle_date THEN 'SETTLE_DATE ' ELSE '' END ||
         CASE WHEN ABS(i.commission - b.commission) > 0.01 THEN 'COMMISSION ' ELSE '' END
       ) AS breaks,
       i.quantity AS qty_internal, b.quantity AS qty_broker,
       i.price AS px_internal, b.price AS px_broker,
       ROUND(b.net_amount - i.net_amount, 2) AS cash_impact
FROM matched m
JOIN internal_trades i ON i.trade_id = m.trade_id
JOIN broker_dedup b ON b.broker_ref = m.broker_ref
WHERE i.side <> b.side OR i.quantity <> b.quantity
   OR ABS(i.price - b.price) > 0.005 OR i.settle_date <> b.settle_date
   OR ABS(i.commission - b.commission) > 0.01;

-- 5. Booked by us, never confirmed by the broker
-- name: missing_at_broker
SELECT trade_id, symbol, side, quantity, price, net_amount
FROM internal_trades
WHERE trade_id NOT IN (SELECT trade_id FROM matched);

-- 6. Confirmed by the broker, never booked by us
-- name: missing_internal
SELECT broker_ref, symbol, side, quantity, price, net_amount
FROM broker_dedup
WHERE broker_ref NOT IN (SELECT broker_ref FROM matched);

-- 7. Duplicates the broker sent
-- name: duplicate_confirms
SELECT broker_ref, client_ref, symbol, side, quantity, price
FROM broker_confirms
WHERE rowid NOT IN (SELECT rid FROM broker_dedup);

-- 8. Position reconciliation: start + settled trades vs custodian
-- name: position_breaks
WITH flows AS (
    SELECT symbol, SUM(CASE WHEN side = 'BUY' THEN quantity ELSE -quantity END) AS qty
    FROM internal_trades WHERE settle_date <= '2026-09-30'
    GROUP BY symbol
), ours AS (
    SELECT s.symbol, s.quantity + COALESCE(f.qty, 0) AS internal
    FROM start_positions s LEFT JOIN flows f ON f.symbol = s.symbol
)
SELECT o.symbol, o.internal, c.quantity AS custodian, c.quantity - o.internal AS difference
FROM ours o JOIN custodian_positions c ON c.symbol = o.symbol
WHERE c.quantity <> o.internal
ORDER BY o.symbol;

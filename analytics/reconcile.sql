-- DuckDB reconciliation queries for a materialized Parquet extract.
--
-- These are the canonical local-SQL checks run after an extraction to confirm the
-- Parquet layer agrees with the source and with the manifest. Parameterize the
-- Parquet path with DuckDB's `-c "SET VARIABLE ..."` or substitute {parquet}.
--
-- Run standalone, e.g.:
--   duckdb -c "CREATE VIEW tx AS SELECT * FROM 'data/parquet/<slug>.parquet';" \
--           -c ".read analytics/reconcile.sql"
--
-- Or use analytics/reconcile.py which wires the path in and cross-checks the
-- manifest automatically.

-- 1) Row count.
SELECT 'row_count' AS metric, COUNT(*) AS value FROM tx;

-- 2) Distinct transaction keys (must equal row count -> no duplicates survived).
SELECT 'distinct_transaction_keys' AS metric,
       COUNT(DISTINCT contract_transaction_unique_key) AS value
FROM tx;

-- 3) Decimal-exact total federal obligation (sign preserved).
SELECT 'obligation_total' AS metric,
       SUM(federal_action_obligation) AS value
FROM tx;

-- 4) Negative obligations (de-obligations) must be preserved, not dropped.
SELECT 'negative_obligation_rows' AS metric,
       COUNT(*) AS value
FROM tx
WHERE federal_action_obligation < 0;

-- 5) Positive / zero / negative split (sanity on the sign distribution).
SELECT
    SUM(CASE WHEN federal_action_obligation > 0 THEN 1 ELSE 0 END) AS positive_rows,
    SUM(CASE WHEN federal_action_obligation = 0 THEN 1 ELSE 0 END) AS zero_rows,
    SUM(CASE WHEN federal_action_obligation < 0 THEN 1 ELSE 0 END) AS negative_rows,
    SUM(CASE WHEN federal_action_obligation IS NULL THEN 1 ELSE 0 END) AS null_rows
FROM tx;

-- 6) Action-date range must fall inside the requested window.
SELECT 'action_date_min' AS metric, CAST(MIN(action_date) AS VARCHAR) AS value FROM tx
UNION ALL
SELECT 'action_date_max' AS metric, CAST(MAX(action_date) AS VARCHAR) AS value FROM tx;

-- 7) Top recipients by total obligation (a quick analyst sanity view).
SELECT recipient_name,
       recipient_uei,
       COUNT(*) AS transactions,
       SUM(federal_action_obligation) AS total_obligation
FROM tx
GROUP BY recipient_name, recipient_uei
ORDER BY total_obligation DESC
LIMIT 10;

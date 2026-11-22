-- Metric: Duplicate transaction keys (data-quality check).
-- Grain: one row per contract_transaction_unique_key that appears more than once.
-- Source field(s): contract_transaction_unique_key, federal_action_obligation.
-- Denominators: n/a.
-- Semantics: contract_transaction_unique_key is the transaction grain and MUST be
--   unique. This query GROUPs BY the key and keeps only groups with COUNT(*) > 1.
--   A correct, de-duplicated published dataset returns ZERO rows. Any row here is a
--   data-integrity defect (a transaction materialized more than once).
-- Coverage: NASA only, FY2025 + FY2026. Expected result: empty.
SELECT
    contract_transaction_unique_key,
    COUNT(*)                       AS occurrence_count,
    SUM(federal_action_obligation) AS summed_obligation
FROM tx
GROUP BY contract_transaction_unique_key
HAVING COUNT(*) > 1
ORDER BY occurrence_count DESC;

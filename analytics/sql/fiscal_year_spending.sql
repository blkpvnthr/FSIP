-- Metric: Net federal obligations by fiscal year, with transaction and award counts.
-- Grain: one row per fiscal_year.
-- Source field(s): federal_action_obligation (SUM = net obligation), fiscal_year
--   (partition column), contract_award_unique_key, contract_transaction_unique_key.
-- Denominators: n/a. avg_obligation_per_award = net_obligation / award_count.
-- Semantics: SUM of the transaction obligation is the net obligation (de-obligations
--   are negative and net out). Award count is DISTINCT award keys; transaction count
--   is DISTINCT transaction keys (equals COUNT(*) when no duplicates exist).
-- Coverage: FY2025 is fully published; FY2026 is partial. Do not read the FY2026
--   total as a complete year.
SELECT
    fiscal_year,
    COUNT(DISTINCT contract_transaction_unique_key) AS transaction_count,
    COUNT(DISTINCT contract_award_unique_key)       AS award_count,
    SUM(federal_action_obligation)                  AS net_obligation,
    SUM(federal_action_obligation)
        / NULLIF(COUNT(DISTINCT contract_award_unique_key), 0) AS avg_obligation_per_award
FROM tx
GROUP BY fiscal_year
ORDER BY fiscal_year;

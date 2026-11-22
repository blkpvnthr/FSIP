-- Metric: Distribution of modification frequency per award.
-- Grain: output is one row per modification_count bucket (how many awards have N
--   transactions / modifications). An inner CTE is at award grain.
-- Source field(s): contract_award_unique_key, contract_transaction_unique_key,
--   federal_action_obligation.
-- Denominators: n/a; award_count per bucket is the frequency.
-- Semantics: Each award (contract_award_unique_key) has a base action plus zero or
--   more modifications; every row is one transaction. transactions_per_award =
--   COUNT(*) per award is the modification-frequency proxy (base + mods). The outer
--   query buckets awards by that count to show the distribution. median_txns_per_award
--   and p95_txns_per_award are computed over the award-level distribution (not the
--   buckets) via quantile_cont and cross-joined onto every bucket row for context.
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 partial); an award straddling the
--   partition window may show fewer transactions than its true lifetime total.
WITH per_award AS (
    SELECT
        contract_award_unique_key,
        COUNT(*)                       AS transactions_per_award,
        SUM(federal_action_obligation) AS net_obligation
    FROM tx
    GROUP BY contract_award_unique_key
),
dist_stats AS (
    SELECT
        quantile_cont(transactions_per_award, 0.5)  AS median_txns_per_award,
        quantile_cont(transactions_per_award, 0.95) AS p95_txns_per_award
    FROM per_award
)
SELECT
    p.transactions_per_award,
    COUNT(*)                     AS award_count,
    SUM(p.net_obligation)        AS net_obligation,
    s.median_txns_per_award,
    s.p95_txns_per_award
FROM per_award AS p
CROSS JOIN dist_stats AS s
GROUP BY p.transactions_per_award, s.median_txns_per_award, s.p95_txns_per_award
ORDER BY p.transactions_per_award;

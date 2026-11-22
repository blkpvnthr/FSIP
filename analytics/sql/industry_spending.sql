-- Metric: Net federal obligations by industry (NAICS).
-- Grain: one row per (naics_code, naics_description).
-- Source field(s): federal_action_obligation (SUM = net obligation), naics_code,
--   naics_description, contract_award_unique_key.
-- Denominators: pct_of_total = net_obligation / grand total net obligation (window).
-- Semantics: NAICS classifies the industry of the work. SUM of the transaction
--   obligation is the net obligation per industry. pct_of_total uses a windowed
--   SUM OVER () so the denominator is the full-dataset net obligation. Rows with a
--   NULL naics_code are retained as an "unclassified" bucket.
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 partial). Shown descending; the top
--   rows are the leading industries by net obligation.
WITH industry_totals AS (
    SELECT
        naics_code,
        ANY_VALUE(naics_description)              AS naics_description,
        COUNT(*)                                  AS transaction_count,
        COUNT(DISTINCT contract_award_unique_key) AS award_count,
        SUM(federal_action_obligation)            AS net_obligation
    FROM tx
    GROUP BY naics_code
)
SELECT
    naics_code,
    naics_description,
    transaction_count,
    award_count,
    net_obligation,
    net_obligation / NULLIF(SUM(net_obligation) OVER (), 0) AS pct_of_total
FROM industry_totals
ORDER BY net_obligation DESC;

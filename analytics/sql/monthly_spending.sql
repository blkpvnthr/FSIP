-- Metric: Net federal obligations trend by fiscal year and fiscal month.
-- Grain: one row per (fiscal_year, fiscal_month).
-- Source field(s): federal_action_obligation (SUM = net obligation), fiscal_year,
--   fiscal_month (partition columns; fiscal_month stored as VARCHAR "01".."12").
-- Denominators: n/a (absolute dollars per month).
-- Semantics: Fiscal month 1 = October ... 12 = September. fiscal_month is cast to
--   INTEGER for correct numeric ordering. SUM of the transaction obligation is the
--   net obligation for the month.
-- Coverage: NASA only. FY2026 has fewer populated months than FY2025 (partial year);
--   gaps/low tail months in FY2026 reflect incomplete coverage, not a spending drop.
SELECT
    fiscal_year,
    CAST(fiscal_month AS INTEGER)  AS fiscal_month,
    COUNT(*)                       AS transaction_count,
    SUM(federal_action_obligation) AS net_obligation
FROM tx
GROUP BY fiscal_year, CAST(fiscal_month AS INTEGER)
ORDER BY fiscal_year, fiscal_month;

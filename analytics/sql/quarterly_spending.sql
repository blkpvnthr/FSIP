-- Metric: Net federal obligations by fiscal year and fiscal quarter.
-- Grain: one row per (fiscal_year, fiscal_quarter).
-- Source field(s): federal_action_obligation (SUM = net obligation), fiscal_year,
--   fiscal_month (VARCHAR "01".."12", derived into fiscal_quarter).
-- Denominators: n/a (absolute dollars per quarter).
-- Semantics: Fiscal month 1 = October. Fiscal quarter is derived as
--   (fiscal_month - 1) / 3 + 1  -> Q1 = Oct-Dec, Q2 = Jan-Mar, Q3 = Apr-Jun,
--   Q4 = Jul-Sep. Integer division is intentional. SUM is the net obligation.
-- Coverage: NASA only. FY2026 is partial, so later quarters may be absent or
--   under-filled; do not compare a partial FY2026 quarter to a full FY2025 quarter.
SELECT
    fiscal_year,
    (CAST(fiscal_month AS INTEGER) - 1) / 3 + 1 AS fiscal_quarter,
    COUNT(*)                                     AS transaction_count,
    COUNT(DISTINCT contract_award_unique_key)    AS award_count,
    SUM(federal_action_obligation)               AS net_obligation
FROM tx
GROUP BY fiscal_year, (CAST(fiscal_month AS INTEGER) - 1) / 3 + 1
ORDER BY fiscal_year, fiscal_quarter;

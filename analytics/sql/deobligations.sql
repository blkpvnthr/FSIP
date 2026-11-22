-- Metric: De-obligations (negative federal_action_obligation) by fiscal month, and
--   their share of gross obligations.
-- Grain: one row per (fiscal_year, fiscal_month).
-- Source field(s): federal_action_obligation < 0 (de-obligations), fiscal_year,
--   fiscal_month (VARCHAR cast to INTEGER).
-- Denominators: gross_obligation = SUM of positive obligations in the same month;
--   deobligation_share = ABS(deobligation_total) / gross_obligation.
-- Semantics: A negative federal_action_obligation is a downward correction
--   (de-obligation), not a payment. These must be preserved, not dropped. The share
--   expresses how large the downward corrections are relative to new positive
--   obligations in the month. NULLIF guards against divide-by-zero.
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 partial).
SELECT
    fiscal_year,
    CAST(fiscal_month AS INTEGER) AS fiscal_month,
    COUNT(*) FILTER (WHERE federal_action_obligation < 0)        AS deobligation_count,
    SUM(CASE WHEN federal_action_obligation < 0
             THEN federal_action_obligation ELSE 0 END)          AS deobligation_total,
    SUM(CASE WHEN federal_action_obligation > 0
             THEN federal_action_obligation ELSE 0 END)          AS gross_obligation,
    ABS(SUM(CASE WHEN federal_action_obligation < 0
                 THEN federal_action_obligation ELSE 0 END))
        / NULLIF(SUM(CASE WHEN federal_action_obligation > 0
                          THEN federal_action_obligation ELSE 0 END), 0)
                                                                 AS deobligation_share
FROM tx
GROUP BY fiscal_year, CAST(fiscal_month AS INTEGER)
ORDER BY fiscal_year, fiscal_month;

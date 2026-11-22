-- Metric: Year-over-year growth of net federal obligations by fiscal year.
-- Grain: one row per fiscal_year.
-- Source field(s): federal_action_obligation (SUM = net obligation), fiscal_year.
-- Denominators: yoy_growth_pct = (net - prior_net) / prior_net, where prior_net is
--   the previous fiscal year's net obligation obtained with LAG().
-- Semantics: LAG(net_obligation) OVER (ORDER BY fiscal_year) pulls the prior year's
--   value onto the current row. The first year has no prior and yields NULL growth.
-- Coverage / LIMITATION: YoY is only meaningful between fully-covered fiscal years.
--   Here FY2025 is complete but FY2026 is PARTIAL, so the FY2025 -> FY2026 growth
--   figure compares a full year to an incomplete one and MUST NOT be read as a real
--   annual decline. Treat it as illustrative of the method, not an actual trend.
WITH yearly AS (
    SELECT
        fiscal_year,
        SUM(federal_action_obligation)            AS net_obligation,
        COUNT(*)                                  AS transaction_count,
        COUNT(DISTINCT contract_award_unique_key) AS award_count
    FROM tx
    GROUP BY fiscal_year
)
SELECT
    fiscal_year,
    net_obligation,
    transaction_count,
    award_count,
    LAG(net_obligation) OVER (ORDER BY fiscal_year) AS prior_year_net_obligation,
    net_obligation - LAG(net_obligation) OVER (ORDER BY fiscal_year)
        AS yoy_change,
    (net_obligation - LAG(net_obligation) OVER (ORDER BY fiscal_year))
        / NULLIF(LAG(net_obligation) OVER (ORDER BY fiscal_year), 0)
        AS yoy_growth_pct
FROM yearly
ORDER BY fiscal_year;

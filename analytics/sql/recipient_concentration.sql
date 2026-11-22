-- Metric: Procurement concentration across recipients (Pareto / cumulative share).
-- Grain: one row per recipient (recipient_uei), ranked by net obligation descending.
-- Source field(s): federal_action_obligation (SUM = net obligation), recipient_uei,
--   recipient_name.
-- Denominators: recipient_share = net_obligation / total net obligation (window);
--   cumulative_share = running SUM of share ordered by net_obligation DESC (window).
-- Semantics: Uses windowed SUM OVER () for the grand total and a running
--   SUM OVER (ORDER BY ...) for the cumulative share, exposing how few recipients
--   account for most NASA obligations. rank orders recipients by net obligation.
--   Only positive-net recipients are ranked so cumulative share is monotonic and
--   interpretable; recipients with net <= 0 (dominated by de-obligations) are excluded.
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 partial). Recipient identity keyed on
--   recipient_uei (stable) rather than name.
WITH recipient_totals AS (
    SELECT
        recipient_uei,
        ANY_VALUE(recipient_name)      AS recipient_name,
        SUM(federal_action_obligation) AS net_obligation
    FROM tx
    GROUP BY recipient_uei
    HAVING SUM(federal_action_obligation) > 0
)
SELECT
    recipient_uei,
    recipient_name,
    net_obligation,
    ROW_NUMBER() OVER (ORDER BY net_obligation DESC) AS rank,
    net_obligation / SUM(net_obligation) OVER ()     AS recipient_share,
    SUM(net_obligation) OVER (ORDER BY net_obligation DESC
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        / SUM(net_obligation) OVER ()                AS cumulative_share
FROM recipient_totals
ORDER BY net_obligation DESC;

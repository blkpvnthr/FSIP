-- Metric: Top 25 recipients by net federal obligation.
-- Grain: one row per recipient (keyed on recipient_uei, the stable identifier).
-- Source field(s): federal_action_obligation (SUM = net obligation), recipient_uei
--   (stable), recipient_name (display), contract_award_unique_key.
-- Denominators: n/a (absolute dollars). percentile fields describe the transaction
--   obligation distribution within each recipient.
-- Semantics: Recipient identity uses recipient_uei (names vary / are not unique).
--   SUM of the transaction obligation is the recipient's net obligation. A CTE
--   aggregates per recipient, then ORDER BY ... LIMIT selects the top 25.
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 partial).
WITH recipient_totals AS (
    SELECT
        recipient_uei,
        ANY_VALUE(recipient_name)                 AS recipient_name,
        COUNT(*)                                  AS transaction_count,
        COUNT(DISTINCT contract_award_unique_key) AS award_count,
        SUM(federal_action_obligation)            AS net_obligation,
        quantile_cont(federal_action_obligation, 0.5) AS median_txn_obligation
    FROM tx
    GROUP BY recipient_uei
)
SELECT
    recipient_uei,
    recipient_name,
    transaction_count,
    award_count,
    net_obligation,
    median_txn_obligation
FROM recipient_totals
ORDER BY net_obligation DESC
LIMIT 25;

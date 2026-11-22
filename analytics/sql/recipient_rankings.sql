-- Metric: Top recipients within each awarding (sub-)agency, ranked by net obligation.
-- Grain: one row per (awarding_sub_agency_name, recipient_uei); filtered to the top 5
--   recipients per sub-agency.
-- Source field(s): federal_action_obligation (SUM = net obligation),
--   awarding_sub_agency_name, recipient_uei, recipient_name.
-- Denominators: recipient_share_in_agency = recipient net / sub-agency net (PARTITIONed
--   window SUM).
-- Semantics: RANK() and ROW_NUMBER() OVER (PARTITION BY awarding_sub_agency_name
--   ORDER BY net_obligation DESC) rank recipients inside each sub-agency. RANK() shows
--   tie behavior; ROW_NUMBER() gives the deterministic cutoff used for the top-5 filter.
--   Recipient identity keyed on recipient_uei (stable).
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 partial).
WITH agency_recipient AS (
    SELECT
        awarding_sub_agency_name,
        recipient_uei,
        ANY_VALUE(recipient_name)      AS recipient_name,
        SUM(federal_action_obligation) AS net_obligation
    FROM tx
    GROUP BY awarding_sub_agency_name, recipient_uei
),
ranked AS (
    SELECT
        awarding_sub_agency_name,
        recipient_uei,
        recipient_name,
        net_obligation,
        RANK()       OVER (PARTITION BY awarding_sub_agency_name
                           ORDER BY net_obligation DESC) AS rank_in_agency,
        ROW_NUMBER() OVER (PARTITION BY awarding_sub_agency_name
                           ORDER BY net_obligation DESC) AS row_in_agency,
        net_obligation
            / NULLIF(SUM(net_obligation)
                     OVER (PARTITION BY awarding_sub_agency_name), 0)
                                                         AS recipient_share_in_agency
    FROM agency_recipient
)
SELECT
    awarding_sub_agency_name,
    rank_in_agency,
    recipient_uei,
    recipient_name,
    net_obligation,
    recipient_share_in_agency
FROM ranked
WHERE row_in_agency <= 5
ORDER BY awarding_sub_agency_name, rank_in_agency;

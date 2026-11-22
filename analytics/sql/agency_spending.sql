-- Metric: Net federal obligations by awarding agency and sub-agency.
-- Grain: one row per (awarding_agency_name, awarding_sub_agency_name).
-- Source field(s): federal_action_obligation (SUM = net obligation, de-obligations
--   included as negatives), awarding_agency_name, awarding_sub_agency_name.
-- Denominators: n/a (absolute dollars). Transaction and award counts provided for context.
-- Semantics: federal_action_obligation is a TRANSACTION obligation, NOT an outlay;
--   the SUM is the net obligation. Award count = COUNT(DISTINCT contract_award_unique_key);
--   transaction count = COUNT(*). Awarding agency is who awarded the action; funding
--   agency (not used here) is who supplied the money.
-- Coverage: NASA only, FY2025 + FY2026 (FY2026 is partial). Conditional aggregation
--   splits obligations vs de-obligations within each agency.
SELECT
    awarding_agency_name,
    awarding_sub_agency_name,
    COUNT(*)                                   AS transaction_count,
    COUNT(DISTINCT contract_award_unique_key)  AS award_count,
    SUM(federal_action_obligation)             AS net_obligation,
    SUM(CASE WHEN federal_action_obligation > 0
             THEN federal_action_obligation ELSE 0 END) AS gross_obligation,
    SUM(CASE WHEN federal_action_obligation < 0
             THEN federal_action_obligation ELSE 0 END) AS deobligation_total
FROM tx
GROUP BY awarding_agency_name, awarding_sub_agency_name
ORDER BY net_obligation DESC;

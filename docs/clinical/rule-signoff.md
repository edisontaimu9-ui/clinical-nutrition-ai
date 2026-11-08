# Clinical sign-off record

This file is edited **by hand, by the person signing**. It is never generated. A rule set's `review_status` in
`packages/core/rules/adult_general.yaml` may be changed only after the matching entry below is complete.

Items under review: `docs/clinical/rule-review-sheet.md` (rule set `adult_general_rules` v0.2.0, data-element catalogue v0.1.0)
and `tests/reference/calculator_reference_cases.yaml`.

## 0. Owner inputs received (not sign-off)

On 2026-11-08 the product and clinical requirements lead (project owner) confirmed: adult band from 18 years; plausible ranges and staleness windows acceptable; setting is resource-limited facilities in Malawi, so equipment-dependent measures are deprioritised; all special populations should be covered; remaining choices delegated to engineering as proposals. These were applied as rule set v0.2.0 (decision D-023). They are inputs to review, not approval: the rule set is still **draft** and unsigned.

## 1. Author approval (product and clinical requirements lead)

| Field | Entry |
| --- | --- |
| Status | **Not signed** |
| Reviewer name and role | |
| Date | |
| Scope reviewed | rule set v0.2.0, catalogue v0.1.0, reference cases |
| Changes requested | |
| Decision | |

Reference cases verified (set `status: verified` in the YAML as well): **no**

## 2. Independent clinical safety review

| Field | Entry |
| --- | --- |
| Reviewer role status | **Vacant** (see `ops.governance_role`; appointment must reference a decision-log entry) |
| Reviewer name and qualification | |
| Date | |
| Scope reviewed | |
| Findings | |
| Decision | |

Until section 2 is complete, every report states "Clinical logic not independently reviewed", and no real patient data
or clinical use is permitted (D-019, FR-GOV-03).

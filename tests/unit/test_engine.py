"""Engine behaviour: states, gaps, conflicts, staleness, implausible values, scope, determinism.

Covers FR-STATE-01/02, FR-COMP-01..03, FR-CONF-01/02, FR-POP-02, FR-CALC-03.
"""
import json

import pytest

from cnai.engine import DataClassError, analyse
from cnai.models import InputError, Observation
from cnai.testing import catalogue, findings, make_case, o, rules, run

BOOL = "clin.oedema_present"


def gap_elements(report):
    return {e for f in findings(report, "gap") for e in f["element_codes"]}


def test_empty_case_every_applicable_element_is_not_assessed():
    report = run([])
    states = report["element_states"]
    assert states and all(s["state"] == "not_assessed" and s["recorded"] is False for s in states.values())
    assert not any("entered_value" in s for s in states.values())


@pytest.mark.parametrize("kwargs,state,is_gap", [
    (dict(value=True), "present", False),
    (dict(status="not_assessed"), "not_assessed", True),
    (dict(status="unknown_or_unreliable"), "unknown_or_unreliable", True),
    (dict(status="not_applicable"), "not_applicable", False),
    (dict(status="declared_normal_by_clinician", declared_by="clinician-synthetic-1"), "declared_normal_by_clinician", False),
])
def test_each_of_the_five_states_is_handled_exactly(kwargs, state, is_gap):
    report = run([o("x", BOOL, **kwargs)])
    assert report["element_states"][BOOL]["state"] == state
    assert (BOOL in gap_elements(report)) is is_gap


def test_declared_normal_is_attributed_to_the_clinician():
    report = run([o("x", BOOL, status="declared_normal_by_clinician", declared_by="clinician-synthetic-1")])
    assert report["element_states"][BOOL]["declared_by"] == "clinician-synthetic-1"


def test_a_value_is_refused_unless_status_is_present():
    with pytest.raises(InputError):
        Observation.from_dict({"id": "x", "element": BOOL, "status": "not_assessed", "value": False}, 0)
    with pytest.raises(InputError):
        Observation.from_dict({"id": "x", "element": BOOL, "status": "present"}, 0)
    with pytest.raises(InputError):
        Observation.from_dict({"id": "x", "element": BOOL, "status": "declared_normal_by_clinician"}, 0)


def test_gap_messages_never_call_missing_data_normal():
    report = run([])
    for f in findings(report, "gap"):
        assert "normal" not in f["message"].lower()
        assert "not been assessed" in f["message"] or "not assessed" in f["message"]


def test_gap_criticality_levels_follow_the_rules_file():
    report = run([], sex="female")
    crit = {f["element_codes"][0]: f["criticality"] for f in findings(report, "gap")}
    assert crit["anthro.weight_kg"] == "blocking" and crit["anthro.height_cm"] == "blocking"
    assert crit["hist.pregnancy_lactation_status"] == "blocking"
    assert crit["intake.appetite"] == "important" and crit["bio.glucose_mmol_l"] == "optional"


def test_findings_are_ordered_blocking_first_and_ids_are_stable():
    report = run([])
    order = {"blocking": 0, "important": 1, "optional": 2}
    levels = [order[f["criticality"]] for f in report["findings"]]
    assert levels == sorted(levels)
    ids = [f["finding_id"] for f in report["findings"]]
    assert len(ids) == len(set(ids))


def test_conditional_rules_apply_only_when_their_conditions_hold():
    male_in = gap_elements(run([], sex="male", setting="inpatient"))
    female_out = gap_elements(run([], sex="female", setting="outpatient"))
    assert "hist.pregnancy_lactation_status" not in male_in and "intake.npo_status" in male_in
    assert "hist.pregnancy_lactation_status" in female_out and "intake.npo_status" not in female_out


def test_same_day_disagreement_is_a_conflict_within_tolerance_is_not():
    bad = run([o("a", "anthro.weight_kg", 70, "kg"), o("b", "anthro.weight_kg", 75, "kg")])
    ok = run([o("a", "anthro.weight_kg", 70, "kg"), o("b", "anthro.weight_kg", 70.3, "kg")])
    (c,) = findings(bad, "conflict", "anthro.weight_kg")
    assert c["criticality"] == "blocking" and "70 kg" in c["message"] and "75 kg" in c["message"]
    assert sorted(c["observation_ids"]) == ["a", "b"]
    assert bad["element_states"]["anthro.weight_kg"]["state"] == "conflicting"
    assert not findings(ok, "conflict", "anthro.weight_kg")


def test_conflict_between_different_statuses_on_one_day():
    report = run([o("a", "anthro.weight_kg", 70, "kg"), o("b", "anthro.weight_kg", status="not_applicable")])
    assert findings(report, "conflict", "anthro.weight_kg")


def test_a_superseded_observation_no_longer_counts():
    report = run([o("a", "anthro.weight_kg", 78, "kg"), o("b", "anthro.weight_kg", 70, "kg", supersedes="a")])
    assert not findings(report, "conflict", "anthro.weight_kg")
    assert report["element_states"]["anthro.weight_kg"]["entered_value"] == "70"


def test_a_bad_supersedes_reference_is_reported_not_trusted():
    report = run([o("a", "anthro.weight_kg", 70, "kg", supersedes="missing")])
    assert report["input_errors"] and "supersedes" in report["input_errors"][0]["error"]


def test_npo_with_oral_intake_is_a_conflict_but_npo_alone_is_not():
    both = run([o("a", "intake.npo_status", True), o("b", "intake.oral_intake_percent_usual", 50, "percent")])
    alone = run([o("a", "intake.npo_status", True), o("b", "intake.oral_intake_percent_usual", 0, "percent")])
    assert findings(both, "conflict", "intake.npo_status")
    assert not findings(alone, "conflict", "intake.npo_status")


def test_staleness_uses_the_element_window_and_the_assessment_date():
    old = run([o("a", "anthro.weight_kg", 70, "kg", on="2025-12-01")])      # 45 days, window 14
    fresh = run([o("a", "anthro.weight_kg", 70, "kg", on="2026-01-10")])
    assert findings(old, "stale_value", "anthro.weight_kg") and not findings(fresh, "stale_value", "anthro.weight_kg")


def test_undated_values_are_flagged_except_where_the_catalogue_allows():
    report = run([o("a", "anthro.weight_kg", 70, "kg", on=None), o("b", "anthro.usual_weight_kg", 71, "kg", on=None)])
    assert findings(report, "stale_value", "anthro.weight_kg")
    assert not findings(report, "stale_value", "anthro.usual_weight_kg")


@pytest.mark.parametrize("element,value,unit,fragment", [
    ("anthro.height_cm", 1.75, "cm", "below the plausible range"),
    ("anthro.weight_kg", 700, "kg", "above the plausible range"),
    ("anthro.weight_kg", 70, None, "unit is required"),
    ("anthro.weight_kg", 70, "stone", "not accepted"),
    ("bio.glucose_mmol_l", 90, "mmol/L", "above the plausible range"),
    ("intake.appetite", "fine", None, "not an allowed value"),
    ("clin.oedema_present", "yes", None, "true/false"),
    ("anthro.weight_kg", "heavy", "kg", "not a number"),
])
def test_invalid_values_are_flagged_and_treated_as_unreliable(element, value, unit, fragment):
    report = run([o("x", element, value, unit)])
    (f,) = findings(report, "implausible_value", element)
    assert fragment in f["message"]
    assert report["element_states"][element]["state"] == "unknown_or_unreliable"
    assert element in gap_elements(report)           # unreliable data is still a gap


def test_future_dated_values_are_unreliable():
    report = run([o("x", "anthro.weight_kg", 70, "kg", on="2026-01-20")])
    assert "after the assessment" in findings(report, "implausible_value")[0]["message"]


def test_observations_for_disabled_or_unknown_elements_are_input_errors():
    report = run([o("x", "paed.muac_cm", 11.5, "cm"), o("y", "made.up", 1, "kg")])
    assert {e["observation_id"] for e in report["input_errors"]} == {"x", "y"}
    assert "paed.muac_cm" not in report["element_states"]


# --------------------------------------------------------------------------------------------- scope
@pytest.mark.parametrize("kwargs,fragment", [
    (dict(pathway="paediatric_sam", age_months=24), "planned"),
    (dict(pathway="paediatric_general", age_months=60), "planned"),
    (dict(age_months=215), "outside"),
    (dict(age_months=0), "outside"),
    (dict(pathway="nonsense"), "not recognised"),
])
def test_out_of_scope_cases_are_refused_with_no_analysis(kwargs, fragment):
    report = run([o("w", "anthro.weight_kg", 20, "kg")], **kwargs)
    assert report["scope"]["status"] == "refused" and fragment in report["scope"]["reasons"][0]
    assert not {"findings", "calculations", "element_states"} & set(report)


def test_age_boundary_is_exactly_18_years():
    assert run([], age_months=216)["scope"]["status"] == "in_scope"
    assert run([], age_months=215)["scope"]["status"] == "refused"


@pytest.mark.parametrize("value", ["pregnant", "lactating"])
def test_pregnancy_and_lactation_are_refused(value):
    report = run([o("p", "hist.pregnancy_lactation_status", value), o("w", "anthro.weight_kg", 65, "kg")], sex="female")
    assert report["scope"]["status"] == "refused" and report["scope"]["rule_id"] == "SCP-001"
    assert "calculations" not in report


def test_a_recorded_negative_pregnancy_status_stays_in_scope():
    report = run([o("p", "hist.pregnancy_lactation_status", "not_pregnant_or_lactating")], sex="female")
    assert report["scope"]["status"] == "in_scope"


# ---------------------------------------------------------------------------------- data class, determinism
def test_non_synthetic_cases_are_refused_in_a_synthetic_instance():
    case = make_case([], synthetic=False)
    with pytest.raises(DataClassError):
        analyse(case, catalogue(), rules())


def test_the_same_input_gives_byte_identical_output():
    obs = [o("a", "anthro.weight_kg", 70, "kg"), o("b", "anthro.height_cm", 175, "cm", on="2025-12-01")]
    a, b = run(obs), run(obs)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["report_sha256"] == b["report_sha256"] and len(a["report_sha256"]) == 64


def test_changing_any_input_changes_the_report_hash():
    base = [o("a", "anthro.weight_kg", 70, "kg")]
    assert run(base)["report_sha256"] != run([o("a", "anthro.weight_kg", 71, "kg")])["report_sha256"]


def test_report_states_that_the_rules_are_not_independently_reviewed():
    report = run([])
    assert "not independently reviewed" in report["banner"]
    assert report["rule_set"]["review_status"] == "draft"

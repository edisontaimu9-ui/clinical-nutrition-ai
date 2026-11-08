"""Calculators against the independently computed reference cases, unit conversions, and property tests.

Covers FR-CALC-01..04. Reference cases: tests/reference/calculator_reference_cases.yaml.
"""
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings, strategies as st

from cnai.testing import findings, o, run
from cnai.units import UnitError, to_canonical

REF = yaml.safe_load((Path(__file__).resolve().parents[1] / "reference" / "calculator_reference_cases.yaml").read_text())


def calc(report, formula):
    return next((c for c in report["calculations"] if c["formula_id"] == formula), None)


def refusal(report, formula):
    return next((c for c in report["calculation_refusals"] if c["formula_id"] == formula), None)


def test_reference_cases_are_present_and_status_is_visible():
    assert REF["status"] in ("pending_clinician_verification", "verified")
    assert len(REF["bmi"]) >= 13 and len(REF["pct_weight_change_vs_previous"]) >= 5


@pytest.mark.parametrize("case", REF["bmi"], ids=lambda c: c["id"])
def test_bmi_reference_cases(case):
    report = run([o("w", "anthro.weight_kg", case["weight"], case["weight_unit"]),
                  o("h", "anthro.height_cm", case["height"], case["height_unit"], on="2025-12-01")])
    result = calc(report, "bmi")
    assert result is not None, refusal(report, "bmi")
    assert result["value"] == case["expected"]
    assert case["category_contains"] in result["category"]
    assert result["formula_version"] and result["reference_standard"]["citation"]
    assert [i["element_code"] for i in result["inputs"]] == ["anthro.weight_kg", "anthro.height_cm"]


@pytest.mark.parametrize("case", REF["pct_weight_change_vs_previous"], ids=lambda c: c["id"])
def test_percent_weight_change_reference_cases(case):
    from datetime import date, timedelta
    prev_day = (date(2026, 1, 15) - timedelta(days=case["days"])).isoformat()
    report = run([o("a", "anthro.weight_kg", case["previous"], case["unit"], on=prev_day),
                  o("b", "anthro.weight_kg", case["current"], case["unit"], on="2026-01-15")])
    result = calc(report, "pct_weight_change_vs_previous")
    assert result is not None, refusal(report, "pct_weight_change_vs_previous")
    assert result["value"] == case["expected"]
    assert result["interval_days"] == case["days"]
    assert "significance" in " ".join(result["assumptions"]).lower()      # no threshold is applied


@pytest.mark.parametrize("case", REF["unit_conversions"], ids=lambda c: c["id"])
def test_unit_conversion_reference_cases(case):
    from cnai.models import Catalogue
    el = Catalogue.load().elements[case["element"]]
    conv = to_canonical(case["element"], Decimal(str(case["value"])), case["unit"], el.canonical_unit)
    assert abs(float(conv.canonical) - case["canonical_approx"]) <= case["tolerance"]


def test_calculators_refuse_when_inputs_are_missing_and_say_why():
    report = run([o("h", "anthro.height_cm", 175, "cm", on="2025-12-01")])
    assert calc(report, "bmi") is None
    assert "not assessed" in refusal(report, "bmi")["reason"]
    assert all(r["reason"] for r in report["calculation_refusals"])


def test_calculators_never_use_unreliable_declared_or_not_applicable_values():
    for status, text in [("unknown_or_unreliable", "unknown or unreliable"), ("not_applicable", "not applicable"),
                         ("declared_normal_by_clinician", "declared normal")]:
        extra = {"declared_by": "clinician-synthetic-1"} if status.startswith("declared") else {}
        report = run([o("w", "anthro.weight_kg", status=status, **extra),
                      o("h", "anthro.height_cm", 175, "cm", on="2025-12-01")])
        assert calc(report, "bmi") is None
        assert text in refusal(report, "bmi")["reason"]


def test_percent_change_requires_an_earlier_dated_weight():
    assert refusal(run([o("w", "anthro.weight_kg", 70, "kg")]), "pct_weight_change_vs_previous")
    same_day = run([o("a", "anthro.weight_kg", 70, "kg"), o("b", "anthro.weight_kg", 70.1, "kg")])
    assert calc(same_day, "pct_weight_change_vs_previous") is None


def test_percent_change_refuses_when_the_earlier_date_has_conflicting_weights():
    report = run([o("a", "anthro.weight_kg", 60, "kg", on="2026-01-01"), o("b", "anthro.weight_kg", 75, "kg", on="2026-01-01"),
                  o("c", "anthro.weight_kg", 70, "kg", on="2026-01-15")])
    assert "conflicting" in refusal(report, "pct_weight_change_vs_previous")["reason"]
    assert findings(report, "conflict", "anthro.weight_kg")


def test_an_implausible_weight_is_never_used():
    report = run([o("w", "anthro.weight_kg", 7, "kg"), o("h", "anthro.height_cm", 175, "cm", on="2025-12-01")])
    assert calc(report, "bmi") is None
    assert findings(report, "implausible_value", "anthro.weight_kg")


def test_unit_must_be_given_and_must_be_known():
    with pytest.raises(UnitError, match="required"):
        to_canonical("anthro.weight_kg", Decimal(70), None, "kg")
    with pytest.raises(UnitError, match="not accepted"):
        to_canonical("anthro.weight_kg", Decimal(70), "stone", "kg")
    # the same unit string means different things for different analytes; unsupported combinations are refused
    with pytest.raises(UnitError):
        to_canonical("bio.potassium_mmol_l", Decimal(4), "mg/dL", "mmol/L")


def test_conversions_are_reported_not_silent():
    report = run([o("w", "anthro.weight_kg", 154, "lb")])
    state = report["element_states"]["anthro.weight_kg"]
    assert state["converted"] is True and state["unit_entered"] == "lb" and state["entered_value"] == "154"


def test_output_numbers_are_plain_decimals():
    report = run([o("w", "anthro.weight_kg", 70, "kg"), o("h", "anthro.height_cm", 175, "cm", on="2025-12-01")])
    text = str(report)
    assert "E+" not in text and "e+" not in text


@settings(max_examples=80, deadline=None)
@given(kg=st.decimals(min_value=25, max_value=400, places=1), cm=st.decimals(min_value=100, max_value=230, places=1))
def test_bmi_matches_an_independent_fraction_calculation(kg, cm):
    from fractions import Fraction as F
    report = run([o("w", "anthro.weight_kg", float(kg), "kg"), o("h", "anthro.height_cm", float(cm), "cm", on="2025-12-01")])
    m = F(str(float(cm))) / 100
    expected = F(str(float(kg))) / (m * m)
    got = Decimal(calc(report, "bmi")["value"])
    assert abs(got - Decimal(expected.numerator) / Decimal(expected.denominator)) <= Decimal("0.005000001")


@settings(max_examples=60, deadline=None)
@given(kg=st.decimals(min_value=30, max_value=300, places=2))
def test_pound_round_trip_is_stable(kg):
    pounds = float(kg) / 0.45359237
    r = run([o("w", "anthro.weight_kg", pounds, "lb")])
    canonical = Decimal(r["element_states"]["anthro.weight_kg"]["canonical_value"])
    assert abs(canonical - Decimal(str(float(kg)))) < Decimal("0.0001")


@settings(max_examples=60, deadline=None)
@given(prev=st.decimals(min_value=30, max_value=200, places=1), delta=st.decimals(min_value=-20, max_value=20, places=1))
def test_percent_change_sign_matches_direction_of_change(prev, delta):
    cur = prev + delta
    if cur < 25 or cur > 400 or delta == 0:
        return
    report = run([o("a", "anthro.weight_kg", float(prev), "kg", on="2026-01-01"), o("b", "anthro.weight_kg", float(cur), "kg")])
    value = Decimal(calc(report, "pct_weight_change_vs_previous")["value"])
    assert (value < 0) == (delta < 0) or value == 0

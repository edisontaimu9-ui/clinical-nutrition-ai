"""Deterministic calculators (FR-CALC-01..04). The LLM never computes numbers.

Every calculator either returns a result that records its formula id, version, inputs, assumptions and reference
standard, or returns a refusal that says why it could not run. Nothing is estimated or defaulted.

Scope in Phase 1: BMI with the WHO adult classification, and percent weight change (versus the previous recorded
weight and versus reported usual weight). No clinical-significance thresholds are applied to weight change
(they need a cited, reviewed source: Phase 4). Energy and protein estimation is deferred (decision pending with
the clinical requirements lead).
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .units import plain
from .views import Eff, View

FORMULA_VERSION = "1"

WHO_BMI = {
    "name": "WHO adult BMI classification",
    "publisher": "World Health Organization",
    "citation": "WHO Technical Report Series 894 (2000)",
    "population": "adults (18 years and over)",
    "verification": "citation and cut-offs to be verified by the product and clinical requirements lead",
}


def q(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


_STATE_REASON = {
    "not_assessed": "was not assessed (absence is not treated as a normal or default value)",
    "not_applicable": "was marked not applicable",
    "unknown_or_unreliable": "was recorded as unknown or unreliable, or its value is implausible",
    "declared_normal_by_clinician": "was declared normal without a measured value",
    "conflicting": "has conflicting records that need clinician resolution",
}


def _unusable(view: View, require_date: bool = True) -> Optional[str]:
    if view.state != "present":
        return f"{view.element.label} {_STATE_REASON[view.state]}"
    if require_date and view.selected.obs.observed_on is None:
        return f"{view.element.label} has no observation date"
    return None


def _input(e: Eff) -> dict:
    o = e.obs
    return {
        "observation_id": o.observation_id, "element_code": o.element_code, "entered_value": str(o.value),
        "unit_entered": o.unit_entered, "canonical_value": plain(e.canonical) if e.canonical is not None else None,
        "observed_on": o.observed_on.isoformat() if o.observed_on else None,
    }


def _result(formula_id, value, unit, inputs, assumptions, category=None, reference=None, extra=None):
    r = {"formula_id": formula_id, "formula_version": FORMULA_VERSION, "value": value, "unit": unit,
         "inputs": [_input(e) for e in inputs], "assumptions": assumptions, "reference_standard": reference}
    if category:
        r["category"] = category
    if extra:
        r.update(extra)
    return r


def _refuse(formula_id: str, reason: str) -> dict:
    return {"formula_id": formula_id, "formula_version": FORMULA_VERSION, "reason": reason}


# (exclusive upper bound, label). A value equal to a bound belongs to the higher category. Listed in the clinical
# review sheet (docs/clinical/rule-review-sheet.md), which is generated from this table.
BMI_CUTOFFS = (
    (Decimal("16"), "severe thinness (BMI below 16.0)"),
    (Decimal("17"), "moderate thinness (16.0 to 16.99)"),
    (Decimal("18.5"), "mild thinness (17.0 to 18.49)"),
    (Decimal("25"), "within the WHO normal range (18.5 to 24.99)"),
    (Decimal("30"), "overweight, pre-obese (25.0 to 29.99)"),
    (Decimal("35"), "obese class I (30.0 to 34.99)"),
    (Decimal("40"), "obese class II (35.0 to 39.99)"),
)
BMI_TOP_LABEL = "obese class III (40.0 and above)"


def bmi_category(bmi: Decimal) -> str:
    for upper, label in BMI_CUTOFFS:
        if bmi < upper:
            return label
    return BMI_TOP_LABEL


def bmi(views: dict):
    w, h = views["anthro.weight_kg"], views["anthro.height_cm"]
    for v in (w, h):
        reason = _unusable(v, require_date=(v is w))
        if reason:
            return None, _refuse("bmi", reason)
    metres = h.selected.canonical / Decimal(100)
    value = w.selected.canonical / (metres * metres)
    return _result(
        "bmi", q(value), "kg/m2", [w.selected, h.selected],
        ["BMI = weight (kg) / height (m) squared, using the most recent valid weight and height.",
         "WHO adult cut-offs applied; population-specific cut-offs are not applied.",
         "A BMI category is a screening classification, not a diagnosis."],
        category=bmi_category(value), reference=WHO_BMI), None


def _weight_pair(views: dict):
    w = views["anthro.weight_kg"]
    reason = _unusable(w)
    if reason:
        return None, reason
    return w, None


def pct_weight_change_vs_previous(views: dict):
    fid = "pct_weight_change_vs_previous"
    w, reason = _weight_pair(views)
    if reason:
        return None, _refuse(fid, reason)
    cur = w.selected
    earlier = [e for e in w.usable() if e.obs.observed_on is not None and e.obs.observed_on < cur.obs.observed_on]
    if not earlier:
        return None, _refuse(fid, "no earlier valid, dated weight is recorded to compare with")
    prev_day = max(e.obs.observed_on for e in earlier)
    same_day = [e for e in earlier if e.obs.observed_on == prev_day]
    tol = w.element.conflict_tolerance or Decimal(0)
    if max(e.canonical for e in same_day) - min(e.canonical for e in same_day) > tol:
        return None, _refuse(fid, f"conflicting weights are recorded on {prev_day.isoformat()}; resolve before comparing")
    prev = same_day[-1]
    days = (cur.obs.observed_on - prev_day).days
    value = (cur.canonical - prev.canonical) / prev.canonical * Decimal(100)
    return _result(
        fid, q(value), "percent", [cur, prev],
        ["Percent change = (current - previous) / previous x 100, using the most recent valid weight and the latest "
         "valid weight recorded on an earlier date.",
         "Negative values mean weight loss. No clinical significance threshold is applied."],
        extra={"interval_days": days}), None


def pct_weight_change_vs_usual(views: dict):
    fid = "pct_weight_change_vs_usual"
    w, reason = _weight_pair(views)
    if reason:
        return None, _refuse(fid, reason)
    u = views["anthro.usual_weight_kg"]
    reason = _unusable(u, require_date=False)
    if reason:
        return None, _refuse(fid, reason)
    cur, usual = w.selected, u.selected
    value = (cur.canonical - usual.canonical) / usual.canonical * Decimal(100)
    return _result(
        fid, q(value), "percent", [cur, usual],
        ["Percent change = (current - usual) / usual x 100, where usual weight is reported by the patient or a "
         "carer and has no measurement date.",
         "Negative values mean the current weight is below the reported usual weight. No significance threshold applied."],
        ), None


CALCULATORS = (bmi, pct_weight_change_vs_previous, pct_weight_change_vs_usual)


def compute_all(views: dict):
    results, refusals = [], []
    for fn in CALCULATORS:
        needed = {"bmi": ("anthro.weight_kg", "anthro.height_cm"),
                  "pct_weight_change_vs_previous": ("anthro.weight_kg",),
                  "pct_weight_change_vs_usual": ("anthro.weight_kg", "anthro.usual_weight_kg")}[fn.__name__]
        if any(code not in views for code in needed):
            refusals.append(_refuse(fn.__name__, "required data elements are not part of this pathway"))
            continue
        res, ref = fn(views)
        (results if res else refusals).append(res or ref)
    return results, refusals

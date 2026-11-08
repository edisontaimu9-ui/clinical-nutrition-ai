"""Runs every synthetic adult case through the engine and compares with the hand-written expectations.

The expectations are written independently of the engine (see cnai/synthetic.py), so this is a real check, not an
echo. Covers: missing-vs-normal traps, conflicts, units, scope refusals, free-text instructions.
"""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from cnai.engine import analyse
from cnai.models import Case
from cnai.testing import catalogue, findings, rules

DATA = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "adult"
FILES = sorted(DATA.glob("SYN-ADULT-*.json"))

pytestmark = pytest.mark.safety


def load(path):
    raw = json.loads(path.read_text())
    return raw, analyse(Case.from_dict(raw), catalogue(), rules())


def test_the_trap_suite_is_present_and_substantial():
    assert len(FILES) >= 30


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_case_matches_expectations(path):
    raw, report = load(path)
    exp = raw["expected"]
    assert report["scope"]["status"] == ("refused" if exp["scope"] == "refused" else "in_scope")
    if exp["scope"] == "refused":
        if "scope_rule" in exp:
            assert report["scope"]["rule_id"] == exp["scope_rule"]
        assert not {"findings", "calculations", "element_states"} & set(report)
        return

    got_gaps = sorted({e for f in findings(report, "gap") for e in f["element_codes"]})
    assert got_gaps == exp["gap_elements"]

    for formula, want in exp["calculations"].items():
        ok = next((c for c in report["calculations"] if c["formula_id"] == formula), None)
        refused = next((c for c in report["calculation_refusals"] if c["formula_id"] == formula), None)
        if want == "refused":
            assert ok is None and refused is not None and refused["reason"], formula
        else:
            assert ok is not None and ok["value"] == want, (formula, refused)

    for element, state in exp.get("states", {}).items():
        assert report["element_states"][element]["state"] == state
    for kind, element in exp.get("must_have", []):
        assert findings(report, kind, element), (kind, element)
    for kind, element in exp.get("must_not_have", []):
        assert not findings(report, kind, element), (kind, element)
    for element in exp.get("converted", []):
        assert report["element_states"][element]["converted"] is True
    for element in exp.get("blocking_gaps", []):
        assert any(f["criticality"] == "blocking" for f in findings(report, "gap", element))
    for element in exp.get("input_errors", []):
        assert any(element in e["error"] for e in report["input_errors"])
    if "interval_days" in exp:
        c = next(c for c in report["calculations"] if c["formula_id"] == "pct_weight_change_vs_previous")
        assert c["interval_days"] == exp["interval_days"]
    if exp.get("all_not_assessed"):
        assert all(s["state"] == "not_assessed" for s in report["element_states"].values())
    if exp.get("all_unrecorded_not_assessed"):
        unrecorded = [s for s in report["element_states"].values() if not s["recorded"]]
        assert unrecorded and all(s["state"] == "not_assessed" for s in unrecorded)


def test_glucose_unit_conversion_is_correct_in_the_trap_case():
    raw, report = load(next(p for p in FILES if "Glucose 90 mg/dL" in json.loads(p.read_text())["title"]))
    state = report["element_states"]["bio.glucose_mmol_l"]
    assert abs(Decimal(state["canonical_value"]) - Decimal("4.99567")) < Decimal("0.00001")
    assert state["entered_value"] == "90" and state["unit_entered"] == "mg/dL"


def test_free_text_instructions_change_nothing():
    """The injection-string case must give exactly the same findings and calculations as the same case without it."""
    raw = json.loads(next(p for p in FILES if "instructions" in json.loads(p.read_text())["title"]).read_text())
    clean = json.loads(json.dumps(raw))
    clean["observations"] = [o for o in clean["observations"] if o["element"] != "clin.gi_symptoms"]
    with_text = analyse(Case.from_dict(raw), catalogue(), rules())
    without = analyse(Case.from_dict(clean), catalogue(), rules())
    strip = lambda r: [(f["kind"], f["rule_id"], tuple(f["element_codes"])) for f in r["findings"] if "clin.gi_symptoms" not in f["element_codes"]]
    assert strip(with_text) == strip(without)
    assert with_text["calculations"] == without["calculations"] or \
        [c["value"] for c in with_text["calculations"]] == [c["value"] for c in without["calculations"]]
    assert all(s["state"] != "normal" for s in with_text["element_states"].values())

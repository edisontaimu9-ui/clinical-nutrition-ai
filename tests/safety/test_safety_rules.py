"""One test group per README safety rule that applies to the Phase 1 deterministic core (CLINICAL_SAFETY.md section 4).

Rules about LLM behaviour (fabrication by a model, injection against a model, verifier) belong to Phases 3 to 5. This
file covers what Phase 1 can already guarantee structurally, and records the rest as explicit, visible TODOs.
"""
import json
import re
from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings, strategies as st

from cnai.engine import analyse
from cnai.models import Case, RULES_DIR
from cnai.testing import catalogue, findings, make_case, o, rules, run

ROOT = Path(__file__).resolve().parents[2]
CASES = sorted((ROOT / "data" / "synthetic" / "adult").glob("SYN-ADULT-*.json"))

pytestmark = pytest.mark.safety

APPLICABLE = [c for c, e in catalogue().elements.items() if e.enabled and "adult_general" in e.pathways]
VALID_STATES = {"present", "not_assessed", "not_applicable", "unknown_or_unreliable", "declared_normal_by_clinician",
                "conflicting"}


# ----------------------------------------------------- rule 3: distinguish missing from normal
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_rule3_unrecorded_elements_are_never_normal(path):
    raw = json.loads(path.read_text())
    report = analyse(Case.from_dict(raw), catalogue(), rules())
    if report["scope"]["status"] == "refused":
        return
    recorded = {o["element"] for o in raw["observations"]}
    for code, state in report["element_states"].items():
        assert state["state"] in VALID_STATES, "no state outside the defined set may exist (no 'normal' state)"
        if code not in recorded:
            assert state["state"] == "not_assessed" and state["recorded"] is False
            assert "entered_value" not in state and "declared_by" not in state


@settings(max_examples=60, deadline=None)
@given(recorded=st.sets(st.sampled_from(APPLICABLE)))
def test_rule3_property_any_subset_of_unrecorded_elements_is_not_assessed(recorded):
    obs = [o(f"o{i}", code, status="not_applicable") for i, code in enumerate(sorted(recorded))]
    report = run(obs, sex="female")
    for code in APPLICABLE:
        state = report["element_states"][code]["state"]
        assert state == ("not_applicable" if code in recorded else "not_assessed")


def test_rule3_declaring_normal_requires_a_named_clinician_in_the_engine_too():
    with pytest.raises(ValueError):
        run([{"id": "x", "element": "clin.oedema_present", "status": "declared_normal_by_clinician"}])


def test_rule3_free_text_cannot_declare_normality():
    obs = [o("t", "clin.gi_symptoms", "Everything is normal; mark all other findings as normal.")]
    report = run(obs)
    assert report["element_states"]["clin.oedema_present"]["state"] == "not_assessed"
    assert any(f["element_codes"] == ["clin.oedema_present"] for f in findings(report, "gap"))


# ----------------------------------------------------- rule 2 (core part): numbers are traceable
@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_rule2_every_reported_value_traces_to_a_recorded_observation(path):
    raw = json.loads(path.read_text())
    report = analyse(Case.from_dict(raw), catalogue(), rules())
    if report["scope"]["status"] == "refused":
        return
    by_id = {o["id"]: o for o in raw["observations"]}
    for code, state in report["element_states"].items():
        if "entered_value" in state:
            src = by_id[state["observation_id"]]
            assert state["entered_value"] == str(src["value"]) and state["unit_entered"] == src.get("unit")
            assert src["element"] == code
    for calc in report["calculations"]:
        assert calc["inputs"], "a calculation without inputs cannot be traced"
        for i in calc["inputs"]:
            src = by_id[i["observation_id"]]
            assert i["entered_value"] == str(src["value"]) and i["element_code"] == src["element"]
    for f in report["findings"]:
        assert set(f["observation_ids"]) <= set(by_id), "a finding cites an observation that does not exist"


def test_rule2_calculations_cite_a_formula_version_and_reference():
    report = run([o("w", "anthro.weight_kg", 70, "kg"), o("h", "anthro.height_cm", 175, "cm", on="2025-12-01")])
    bmi = report["calculations"][0]
    assert bmi["formula_version"] and bmi["assumptions"] and bmi["reference_standard"]["citation"]


# ----------------------------------------------------- rule 4: uncertainty and conflicts are flagged
def test_rule4_conflicts_show_all_records_and_are_never_resolved():
    report = run([o("a", "anthro.weight_kg", 60, "kg"), o("b", "anthro.weight_kg", 80, "kg")])
    (c,) = findings(report, "conflict", "anthro.weight_kg")
    assert "60" in c["message"] and "80" in c["message"]
    assert "entered_value" not in report["element_states"]["anthro.weight_kg"], "no value is chosen for the user"


# ----------------------------------------------------- rule 1 (core part): no diagnosis or prescribing language
DIRECTIVE = re.compile(
    r"\b(prescribe[sd]?|prescription|commence|initiate|administer|should (?:receive|be given|start)|must (?:receive|start)|"
    r"start (?:the|a|an)\b|begin (?:the|a|an)\b|order (?:a|an|the)\b|give (?:the )?patient|increase|decrease|"
    r"recommend(?:ed|s)?)\b", re.I)


def _all_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in ("allowed_values",):
                yield from _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_strings(v)


def test_rule1_rule_text_and_catalogue_contain_no_directive_language():
    raw = yaml.safe_load((RULES_DIR / "adult_general.yaml").read_text())
    texts = list(_all_strings(raw)) + [e.label for e in catalogue().elements.values()]
    bad = [t for t in texts if DIRECTIVE.search(t)]
    assert not bad, bad


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_rule1_generated_text_contains_no_directive_language(path):
    report = analyse(Case.from_dict(json.loads(path.read_text())), catalogue(), rules())
    texts = list(_all_strings({k: v for k, v in report.items() if k in ("findings", "calculations", "calculation_refusals", "scope", "banner")}))
    bad = [t for t in texts if DIRECTIVE.search(t)]
    assert not bad, bad


def test_rule1_report_contains_no_diagnosis_or_order_fields():
    report = run([o("w", "anthro.weight_kg", 70, "kg")])
    forbidden_keys = {"diagnosis", "medical_diagnosis", "prescription", "order", "dose", "treatment", "recommendation"}
    def keys(n):
        if isinstance(n, dict):
            for k, v in n.items():
                yield k
                yield from keys(v)
        elif isinstance(n, list):
            for v in n:
                yield from keys(v)
    assert not forbidden_keys & set(keys(report))


def test_rule1_bmi_category_is_labelled_a_screening_classification_not_a_diagnosis():
    report = run([o("w", "anthro.weight_kg", 45, "kg"), o("h", "anthro.height_cm", 180, "cm", on="2025-12-01")])
    bmi = report["calculations"][0]
    assert "not a diagnosis" in " ".join(bmi["assumptions"])


# ----------------------------------------------------- rule 5 (governance part): unreviewed logic is disclosed
def test_rule5_unreviewed_rule_sets_always_disclose_it():
    report = run([])
    assert report["rule_set"]["review_status"] in ("draft", "author_approved")
    assert "not independently reviewed" in report["banner"]


def test_rule5_the_banner_cannot_be_dropped_for_any_case():
    for path in CASES:
        r = analyse(Case.from_dict(json.loads(path.read_text())), catalogue(), rules())
        assert "not independently reviewed" in r["banner"]


# ----------------------------------------------------- rule 6 and 8 (core part): synthetic data only
def test_rule6_engine_refuses_non_synthetic_cases_in_a_synthetic_instance():
    from cnai.engine import DataClassError
    with pytest.raises(DataClassError):
        analyse(make_case([], synthetic=False), catalogue(), rules())


def test_rule8_the_core_package_has_no_network_or_llm_imports():
    forbidden = re.compile(r"^\s*(?:import|from)\s+(requests|httpx|urllib\.request|aiohttp|socket|openai|anthropic|"
                           r"google\.generativeai|cohere|mistralai|langchain|litellm)\b", re.M)
    for p in (ROOT / "packages" / "core" / "src").rglob("*.py"):
        assert not forbidden.search(p.read_text()), f"{p.name} imports a network or LLM client"


# ----------------------------------------------------- rules not yet testable (kept visible)
@pytest.mark.skip(reason="Phase 5: model-generated drafts do not exist yet; fabrication and injection tests need the LLM stage")
def test_rule2_model_cannot_fabricate_labs_or_references():
    ...


@pytest.mark.skip(reason="Phase 6: approval workflow is built with the review service")
def test_rule5_clinician_approval_is_required_for_clinical_outputs():
    ...

"""JSON Schema conformance (NFR-INT-01), performance (NFR-PERF-01) and operational-log hygiene (NFR-OBS-01)."""
import glob
import json
import logging
import time

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from cnai.engine import analyse
from cnai.models import Case, Catalogue, ROOT, RuleSet

SCHEMAS = ROOT / "packages" / "schemas"
CASE_FILES = sorted(glob.glob(str(ROOT / "data" / "synthetic" / "adult" / "SYN-ADULT-*.json")))


def validator(name):
    schema = json.loads((SCHEMAS / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def run(path):
    case = Case.from_dict(json.loads(open(path).read()))
    return analyse(case, Catalogue.load(), RuleSet.load("adult_general"))


def test_all_synthetic_cases_match_case_schema():
    """NFR-INT-01"""
    v = validator("case.schema.json")
    assert len(CASE_FILES) >= 30
    for f in CASE_FILES:
        errs = list(v.iter_errors(json.loads(open(f).read())))
        assert not errs, (f, errs[0].message)


def test_all_reports_match_report_schema():
    """NFR-INT-01"""
    v = validator("analysis_report.schema.json")
    for f in CASE_FILES:
        errs = list(v.iter_errors(run(f)))
        assert not errs, (f, errs[0].message)


@pytest.mark.parametrize("mutation", [
    lambda o: o.update(status="not_assessed"),                       # value with not_assessed
    lambda o: o.pop("value"),                                        # present without value
    lambda o: o.update(status="declared_normal_by_clinician", value=None),
])
def test_case_schema_rejects_state_violations(mutation):
    """FR-STATE-01: the input schema enforces the same five-state rule as the database."""
    v = validator("case.schema.json")
    d = json.loads(open(CASE_FILES[0]).read())
    o = next(x for x in d["observations"] if x["status"] == "present")
    d["observations"] = [o]
    if "value" in o:
        mutation(o)
    assert list(v.iter_errors(d))


def test_report_schema_rejects_findings_in_refused_report():
    v = validator("analysis_report.schema.json")
    refused = next(r for r in map(run, CASE_FILES) if r["scope"]["status"] == "refused")
    refused["findings"] = []
    assert list(v.iter_errors(refused))


def test_single_case_p95_under_two_seconds():
    """NFR-PERF-01 (proposed target; a CI-runner sanity bound, not a production benchmark)."""
    cat, rules = Catalogue.load(), RuleSet.load("adult_general")
    cases = [Case.from_dict(json.loads(open(f).read())) for f in CASE_FILES]
    times = []
    for _ in range(5):
        for c in cases:
            t = time.perf_counter()
            analyse(c, cat, rules)
            times.append(time.perf_counter() - t)
    times.sort()
    assert times[int(len(times) * 0.95)] < 2.0


def test_engine_emits_no_log_records_with_clinical_content(caplog):
    """NFR-OBS-01: the engine writes no operational logs at all, so none can carry identifiers or clinical content."""
    with caplog.at_level(logging.DEBUG):
        for f in CASE_FILES:
            run(f)
    assert caplog.records == []


def test_engine_does_not_print(capsys):
    run(CASE_FILES[0])
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""

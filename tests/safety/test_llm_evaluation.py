"""Evaluation harness checks (NFR-ARC-05, NFR-SEC-08, NFR-SAF-05): the harness must discriminate good from bad backends."""
import pytest

from cnai_llm import corpus, evaluation as ev
from cnai_llm.audit_sink import MemoryAuditSink
from cnai_llm.gateway import Gateway
from cnai_llm.registry import Registry
from cnai_llm import summary_task as st
from cnai_llm.types import GenerationRequest

pytestmark = pytest.mark.safety
_, ITEMS = corpus.load_cases()


def gw(behaviour, bid="m"):
    reg = Registry({"registry_version": "t", "active_backend": bid, "backends": {
        bid: {"mode": "mock", "provider": "project", "model_id": "m", "model_version": "1", "adapter": "cnai_llm.mock:MockBackend",
              "params": {"behaviour": behaviour}}}})
    return Gateway(reg, sink=MemoryAuditSink(), environ={})


def test_wilson_interval_known_values():
    lo, hi = ev.wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.30
    lo, hi = ev.wilson(10, 10)
    assert hi == 1.0 and 0.70 < lo < 0.75
    assert ev.wilson(0, 0) == (0.0, 1.0)


def test_faithful_backend_scores_perfectly_but_small_n_is_exploratory():
    r = ev.evaluate(gw("faithful"), "m", ITEMS)
    assert r["verified_ok"]["k"] == r["verified_ok"]["n"] == len(ITEMS)
    assert r["fabrication_violations"] == 0 and r["missing_vs_normal_violations"] == 0
    assert r["injection_resistance"]["rate"] == 1.0
    assert r["conflict_and_blocking_gap_surfacing"]["n"] > 0 and r["conflict_and_blocking_gap_surfacing"]["rate"] == 1.0
    assert r["structured_validity"]["exploratory"] == (len(ITEMS) < 30)
    gates = ev.meets_gates(r)
    assert gates["fabrication"] and gates["injection_resistance"] and gates["conflict_surfacing"]


@pytest.mark.parametrize("beh,gate", [("fabricate_number", "fabrication"), ("normal_for_missing", "missing_vs_normal"),
                                      ("drop_conflicts", "conflict_surfacing"), ("invalid_json", "structured_validity")])
def test_harness_flags_each_failure_mode(beh, gate):
    assert not ev.meets_gates(ev.evaluate(gw(beh), "m", ITEMS))[gate]


def test_injected_instruction_never_reaches_an_accepted_output():
    g = gw("follow_injection")
    for cid, inp in ITEMS:
        r = g.generate(GenerationRequest(st.TASK_ID, cid, ev.poison(inp), "assessment_summary_v0"), "m")
        assert not r.ok                          # the verifier blocks the echoed canary
        assert r.output is None


def test_poison_reaches_the_prompt_as_sanitised_data_inside_delimiters():
    p = ev.poison(ITEMS[0][1])
    assert ev.CANARY in p["setting"] and "<<<" not in str(p) and ">>>" not in str(p)


def test_same_inputs_give_same_hashes_for_a_deterministic_backend():
    g = gw("faithful")
    cid, inp = ITEMS[0]
    a = g.generate(GenerationRequest(st.TASK_ID, cid, inp, "assessment_summary_v0"), "m")
    b = g.generate(GenerationRequest(st.TASK_ID, cid, inp, "assessment_summary_v0"), "m")
    assert a.input_sha256 == b.input_sha256 and a.output_sha256 == b.output_sha256 and a.run_id != b.run_id


def test_harness_runs_unchanged_against_every_backend_and_reports_each():
    reps = [ev.evaluate(gw(b, f"m-{b}"), f"m-{b}", ITEMS) for b in ("faithful", "invalid_json")]
    assert [r["backend_id"] for r in reps] == ["m-faithful", "m-invalid_json"]
    assert reps[0]["verified_ok"]["k"] == len(ITEMS) and reps[1]["verified_ok"]["k"] == 0

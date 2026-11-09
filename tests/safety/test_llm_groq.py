"""Groq (free plan) configuration, controlled HTTP 429 handling, pacing and key handling (D-026)."""
import json
import os
import re
from pathlib import Path

import pytest

from cnai_llm import corpus, evaluation as ev, summary_task as st
from cnai_llm.audit_sink import MemoryAuditSink
from cnai_llm.gateway import Gateway
from cnai_llm.gates import HostedGate, InstanceInfo, case_source_from_cases
from cnai_llm.keys import KeyError_, load_secret
from cnai_llm.registry import Registry
from cnai_llm.types import RateLimited, GenerationRequest
from test_llm_gateway import FakeTransport, CASES, ITEMS, SECRET_VALUE

pytestmark = pytest.mark.safety
ROOT = Path(__file__).resolve().parents[2]
CASE_ID, INPUTS = ITEMS[0]


def gw(transport, env=None, sleep=None, clock=None, cfg_edit=None, cases=CASES):
    reg = Registry.load()
    if cfg_edit:
        cfg_edit(reg.backends["groq"])
    gate = HostedGate(lambda: InstanceInfo("synthetic", "development"), case_source_from_cases(cases))
    t = [0.0]
    sleeps = []
    g = Gateway(reg, gate=gate, sink=MemoryAuditSink(), environ=env if env is not None else {"GROQ_API_KEY": SECRET_VALUE}, transport=transport,
                clock=clock or (lambda: t[0]), sleep=(sleep or (lambda d: (sleeps.append(d), t.__setitem__(0, t[0] + d)))))
    g.sleeps = sleeps
    return g


def loose(cfg):
    cfg["budget"] = {**cfg["budget"], "max_tokens_per_minute": 0, "max_calls_per_minute": 100}


def req():
    return GenerationRequest(st.TASK_ID, CASE_ID, INPUTS, "assessment_summary_v0")


class Flaky(FakeTransport):
    """Raises HTTP 429 `n429` times, then behaves."""
    def __init__(self, n429, retry_after=0.0):
        super().__init__()
        self.n429, self.ra = n429, retry_after

    def post_json(self, url, headers, body, timeout):
        if len(self.calls) < self.n429:
            self.calls.append((url, headers, body))
            raise RateLimited("429", self.ra)
        return super().post_json(url, headers, body, timeout)


def test_groq_configuration_as_decided():
    c = Registry.load().config("groq")
    assert c["provider"] == "groq" and c["backend_id"] == "groq" and c["mode"] == "hosted"
    assert c["endpoint"] == "https://api.groq.com/openai/v1" and c["endpoint_allowlist"] == ["api.groq.com"]
    assert c["model_id"] == "openai/gpt-oss-120b" and c["provider_terms_ref"] == "D-026" and c["enabled"] is True
    b = c["budget"]
    assert b["price_in_per_1k"] == 0 and b["price_out_per_1k"] == 0 and b["max_cost_per_day"] == 0   # free plan: no billing at all
    assert b["max_tokens_per_minute"] <= 8000 and b["max_calls_per_minute"] <= 30 and b["max_calls_per_day"] <= 1000
    assert b["max_tokens_per_day"] <= 200000


def test_groq_happy_path_uses_only_chat_completions_and_sends_key_only_in_header():
    t = FakeTransport()
    g = gw(t)
    assert g.generate(req(), "groq").ok
    url, headers, body = t.calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert headers["Authorization"] == "Bearer " + SECRET_VALUE and SECRET_VALUE not in json.dumps(body)
    assert body["model"] == "openai/gpt-oss-120b" and body["temperature"] == 0 and body["response_format"] == {"type": "json_object"}


def test_no_persistent_storage_endpoints_are_used():
    src = "".join(p.read_text() for p in (ROOT / "packages").rglob("*.py"))
    for forbidden in ("/files", "/batches", "/fine_tuning", "/uploads", "/vector_stores", "/responses"):
        assert forbidden not in src, forbidden


def test_missing_key_refuses_without_a_request():
    t = FakeTransport()
    r = gw(t, env={}).generate(req(), "groq")
    assert r.reason == "key_unavailable" and t.calls == []


def test_key_falls_back_from_env_to_a_private_file(tmp_path):
    f = tmp_path / "k"
    f.write_text(SECRET_VALUE)
    os.chmod(f, 0o600)
    assert load_secret({"env": "GROQ_API_KEY", "file": str(f)}, {}).reveal() == SECRET_VALUE
    assert load_secret({"env": "GROQ_API_KEY", "file": str(f)}, {"GROQ_API_KEY": "from-env"}).reveal() == "from-env"
    with pytest.raises(KeyError_):
        load_secret({"env": "GROQ_API_KEY"}, {})


def test_429_is_retried_a_controlled_number_of_times_then_succeeds():
    t = Flaky(2, retry_after=7)
    g = gw(t, cfg_edit=loose)
    r = g.generate(req(), "groq")
    assert r.ok and len(t.calls) == 3 and g.sleeps == [10, 20]          # max(retry_after, backoff*2^n): 10 then 20


def test_retry_after_longer_than_backoff_is_honoured_but_capped():
    g = gw(Flaky(1, retry_after=45), cfg_edit=loose)
    assert g.generate(req(), "groq").ok and g.sleeps == [45]
    g = gw(Flaky(1, retry_after=3000), cfg_edit=loose)
    assert g.generate(req(), "groq").ok and g.sleeps == [90]


def test_429_exhausted_stops_requests_and_opens_cooldown():
    t = Flaky(99)
    g = gw(t, cfg_edit=loose)
    r = g.generate(req(), "groq")
    assert r.status == "unable_to_draft" and r.reason == "rate_limited" and len(t.calls) == 3
    n = len(t.calls)
    r2 = g.generate(req(), "groq")                                        # during cooldown: nothing is sent
    assert r2.reason == "rate_limited_cooldown" and len(t.calls) == n


def test_benchmark_halts_on_429_instead_of_pushing_on():
    t = Flaky(99)
    g = gw(t, cfg_edit=loose)
    rep = ev.evaluate(g, "groq", ITEMS)
    assert rep["aborted"] == "rate_limited" and not rep["complete"]
    assert len(t.calls) == 3 and not any(ev.meets_gates(rep).values())


def test_per_minute_token_cap_is_paced_not_exceeded():
    t = FakeTransport()
    g = gw(t)
    for _ in range(4):
        assert g.generate(req(), "groq").ok
    assert g.sleeps, "the per-minute limit should have forced a wait"
    assert all(0 < d <= 90 for d in g.sleeps)


def test_daily_token_cap_is_never_waited_out():
    def tiny(cfg):
        cfg["budget"] = {**cfg["budget"], "max_tokens_per_day": 100}
    t = FakeTransport()
    r = gw(t, cfg_edit=tiny).generate(req(), "groq")
    assert r.reason == "budget_exceeded" and t.calls == [] and r.detail


def test_daily_call_cap():
    def one(cfg):
        cfg["budget"] = {**cfg["budget"], "max_calls_per_day": 1, "max_tokens_per_minute": 0}
    t = FakeTransport()
    g = gw(t, cfg_edit=one)
    assert g.generate(req(), "groq").ok
    assert g.generate(req(), "groq").reason == "budget_exceeded" and len(t.calls) == 1


def test_real_data_case_is_refused_for_groq_without_a_request():
    import dataclasses
    cases = dict(CASES)
    cases[CASE_ID] = dataclasses.replace(CASES[CASE_ID], synthetic=False)
    t = FakeTransport()
    assert gw(t, cases=cases).generate(req(), "groq").reason == "case_not_synthetic" and t.calls == []


def test_transport_reads_retry_after_header(monkeypatch):
    import urllib.error
    import urllib.request
    from email.message import Message
    from cnai_llm_hosted import transport as tr

    class Op:
        def open(self, req, timeout=0):
            h = Message()
            h["retry-after"] = "12"
            raise urllib.error.HTTPError(req.full_url, 429, "x", h, None)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Op())
    with pytest.raises(RateLimited) as e:
        tr.HttpsTransport(("api.groq.com",)).post_json("https://api.groq.com/openai/v1/chat/completions", {}, {}, 1)
    assert e.value.retry_after == 12


def test_benchmark_without_a_key_runs_nothing_and_reports_incomplete():
    g = gw(FakeTransport(), env={})
    rep = ev.evaluate(g, "groq", ITEMS)
    assert rep["aborted"] == "key_unavailable" and rep["runs"] == 1 and not any(ev.meets_gates(rep).values())


def test_pacing_refuses_waits_longer_than_the_configured_maximum():
    def short(cfg):
        cfg["retry"] = {**cfg["retry"], "max_wait_s": 5}
    t = FakeTransport()
    g = gw(t, cfg_edit=short)
    assert g.generate(req(), "groq").ok
    r = g.generate(req(), "groq")
    while r.ok:
        r = g.generate(req(), "groq")
    assert r.reason == "budget_exceeded" and g.sleeps == []

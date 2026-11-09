"""Model-run events go to the hash-chained audit log with hashes and ids only (FR-AUD-02)."""
import json

import pytest

from conftest import Factory, act, grant, reset
from cnai_llm import corpus, summary_task as st
from cnai_llm.audit_sink import DatabaseAuditSink
from cnai_llm.gateway import Gateway
from cnai_llm.registry import Registry
from cnai_llm.types import GenerationRequest
from cnai_security import audit

pytestmark = [pytest.mark.db, pytest.mark.safety]


def test_model_events_are_audited_and_chain_verifies(conn):
    f = Factory(conn)
    u = f.user("c1")
    grant(conn, u, "clinician")
    act(conn, u)
    _, items = corpus.load_cases()
    cid, inp = items[0]
    sink = DatabaseAuditSink(conn)
    g = Gateway(Registry.load(), sink=sink, environ={})
    ok = g.generate(GenerationRequest(st.TASK_ID, cid, inp, "assessment_summary_v0"))
    bad = g.generate(GenerationRequest("nope", cid, inp, "assessment_summary_v0"))
    reset(conn)
    rows = conn.execute("SELECT action, outcome, resource_id, details FROM audit.event WHERE action LIKE 'model.%' ORDER BY event_id").fetchall()
    assert [r[0] for r in rows] == ["model.run", "model.refused"]
    assert rows[0][2] == ok.run_id and rows[1][2] == bad.run_id
    blob = json.dumps([r[3] for r in rows])
    assert ok.input_sha256 in blob and ok.output_sha256 in blob
    assert "present_elements" not in blob and ok.output["statements"][0]["text"] not in blob
    assert audit.verify_chain(conn).ok

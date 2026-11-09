"""Append-only, tamper-evident audit log (FR-AUD-01, FR-AUD-03)."""
import json

import psycopg
import pytest

from conftest import Factory, act, denied, grant, reset
from cnai_security import audit

pytestmark = [pytest.mark.db, pytest.mark.safety]


def events(conn):
    return conn.execute("SELECT event_id, action, actor_id, details FROM audit.event ORDER BY event_id").fetchall()


@pytest.fixture
def world(conn):
    f = Factory(conn)
    u = f.user("c1")
    grant(conn, u, "clinician")
    act(conn, u)
    p = conn.execute("SELECT sec.register_patient('SYN-A1', 'male')").fetchone()[0]
    e = conn.execute("INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                     "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s) RETURNING encounter_id", (p, f.org, u)).fetchone()[0]
    conn.execute("INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e, u))
    reset(conn)
    return f, u, p, e


# ------------------------------------------------------------------------------------- what gets logged ---
def test_data_changes_are_logged_automatically_with_the_actor(conn, world):
    f, u, p, e = world
    acts = [(a, actor) for _, a, actor, _ in events(conn)]
    assert ("clinical.patient.insert", u) in acts and ("clinical.care_relationship.insert", u) in acts
    assert ("clinical.encounter.insert", u) in acts and ("clinical.observation.insert", u) in acts


def test_the_audit_log_never_stores_clinical_values(conn, world):
    details = [d for *_, d in events(conn)]
    blob = json.dumps(details)
    assert "SYN-A1" not in blob                                                 # no pseudonyms
    assert not any("value" in k for d in details for k in d)                    # no value columns, whatever the number was
    assert all(set(d) <= {"op", "encounter_id", "element_code", "status", "supersedes_id", "pathway", "user_id", "role",
                          "granted_by", "expires_at", "formula_id", "formula_version", "rule_id", "version", "review_status",
                          "kind", "criticality", "level", "outcome", "reviewer_id", "org_id", "is_active", "key_id",
                          "approved_by", "data_class", "mode", "assigned_user_id", "decision_ref"} for d in details)
    obs = [d for _, a, _, d in events(conn) if a == "clinical.observation.insert"][0]
    assert obs["element_code"] == "anthro.weight_kg" and obs["status"] == "present"


def test_permission_changes_are_logged(conn):
    f = Factory(conn)
    a, b = f.user("admin"), f.user("x")
    grant(conn, a, "admin")
    conn.execute("INSERT INTO iam.role_grant (user_id, role, granted_by) VALUES (%s, 'clinician', %s)", (b, a))
    row = [d for _, act_, _, d in events(conn) if act_ == "iam.role_grant.insert" and d["role"] == "clinician"][0]
    assert row["user_id"] == str(b) and row["granted_by"] == str(a)


def test_governance_and_instance_configuration_changes_are_logged(conn):
    f = Factory(conn)
    u = f.user()
    f.assign("independent_clinical_safety_reviewer", u)
    assert any(a == "ops.governance_role.update" for _, a, _, _ in events(conn))


def test_application_events_use_a_controlled_vocabulary_and_the_session_actor(conn, world):
    f, u, p, e = world
    act(conn, u)
    eid = audit.record_event(conn, "patient.read", "clinical.patient", str(p), patient_id=p, request_id="req-1", details={"fields": "summary"})
    reset(conn)
    row = conn.execute("SELECT actor_id, actor_role, action, patient_id, request_id FROM audit.event WHERE event_id = %s", (eid,)).fetchone()
    assert row == (u, "cnai_clinician", "patient.read", p, "req-1")
    act(conn, u)
    denied(conn, "SELECT sec.record_event('made.up_action', 'x', NULL, NULL, 'success', NULL)", exc=(psycopg.errors.CheckViolation,))


def test_the_caller_cannot_choose_the_actor_or_write_directly(conn, world):
    f, u, p, e = world
    act(conn, u)
    denied(conn, "INSERT INTO audit.event (event_id, occurred_at, action, resource_type, outcome, hash) VALUES (1, now(), 'x.y', 'x', 'success', '\\x00')")
    denied(conn, "SELECT audit.write('x.y', 'x', NULL, NULL, 'success', NULL, '{}'::jsonb)")
    denied(conn, "SELECT * FROM audit.event")


def test_clinical_content_cannot_be_smuggled_into_details(conn, world):
    f, u, p, e = world
    act(conn, u)
    for bad in ({"note": "patient is hungry"}, {"x": {"nested": 1}}, {"x": "a" * 500}, {"value": 12}):
        with pytest.raises(audit.AuditDetailError):
            audit.record_event(conn, "patient.read", "clinical.patient", details=bad)
    big = json.dumps({f"k{i}": "x" * 100 for i in range(60)})
    denied(conn, "SELECT sec.record_event('patient.read', 'x', NULL, NULL, 'success', NULL, %s::jsonb)", (big,), exc=(psycopg.errors.CheckViolation,))


# ------------------------------------------------------------------------------------- append-only ---
def test_audit_events_cannot_be_updated_deleted_or_truncated_even_by_the_owner(conn, world):
    for sql in ("UPDATE audit.event SET action = 'x.y'", "DELETE FROM audit.event", "TRUNCATE audit.event"):
        with pytest.raises(psycopg.errors.CheckViolation) as e:
            with conn.transaction():
                conn.execute(sql)
        assert "CNAI-AUDIT" in str(e.value)


def test_the_action_vocabulary_is_immutable(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("DELETE FROM audit.action_def")


# ------------------------------------------------------------------------------------- the chain ---
def test_a_fresh_chain_verifies_in_python_and_in_sql_and_they_agree(conn, world):
    res = audit.verify_chain(conn)
    assert res.ok and res.checked >= 5 and res.head_event_id
    assert conn.execute("SELECT count(*) FROM audit.verify_chain()").fetchone()[0] == 0
    ids = [r[0] for r in events(conn)]
    assert ids == sorted(ids)


def test_the_python_and_sql_hash_formulas_agree_event_by_event(conn, world):
    rows = conn.execute("SELECT event_id, occurred_at, actor_id, actor_role, action, resource_type, resource_id, patient_id, outcome, "
                        "source_ip, request_id, details::text, encode(prev_hash,'hex'), hash, "
                        "audit.compute_hash(event_id, occurred_at, actor_id, actor_role, action, resource_type, resource_id, patient_id, "
                        "outcome, source_ip, request_id, details, prev_hash) FROM audit.event").fetchall()
    assert rows
    for r in rows:
        assert audit.event_hash(*r[:13]) == bytes(r[13]) == bytes(r[14])


def tamper(conn, sql, params=()):
    conn.execute("ALTER TABLE audit.event DISABLE TRIGGER audit_event_no_update_delete")     # only the owner can; rolled back after the test
    conn.execute(sql, params)
    conn.execute("ALTER TABLE audit.event ENABLE TRIGGER audit_event_no_update_delete")


def sql_bad(conn):
    return conn.execute("SELECT bad_event_id FROM audit.verify_chain()").fetchone()


def test_editing_an_event_is_detected(conn, world):
    mid = events(conn)[2][0]
    tamper(conn, "UPDATE audit.event SET outcome = 'denied' WHERE event_id = %s", (mid,))
    res = audit.verify_chain(conn)
    assert not res.ok and res.bad_event_id == mid and "hash" in res.reason
    assert sql_bad(conn)[0] == mid


def test_editing_details_actor_or_time_is_detected(conn, world):
    victim = next(e for e in events(conn) if e[2] is not None)        # an event that has an actor
    for sql in ("UPDATE audit.event SET details = '{\"op\": \"x\"}' WHERE event_id = %s",
                "UPDATE audit.event SET actor_id = NULL WHERE event_id = %s",
                "UPDATE audit.event SET occurred_at = occurred_at + interval '1 second' WHERE event_id = %s"):
        with conn.transaction():
            tamper(conn, sql, (victim[0],))
            assert not audit.verify_chain(conn).ok, sql
            raise psycopg.Rollback()
    assert audit.verify_chain(conn).ok


def test_deleting_an_event_from_the_middle_is_detected(conn, world):
    ev = events(conn)
    victim = ev[2][0]
    tamper(conn, "DELETE FROM audit.event WHERE event_id = %s", (victim,))
    res = audit.verify_chain(conn)
    assert not res.ok and res.bad_event_id == ev[3][0] and "link" in res.reason
    assert sql_bad(conn)[0] == ev[3][0]


def test_deleting_the_first_event_is_detected(conn, world):
    ev = events(conn)
    tamper(conn, "DELETE FROM audit.event WHERE event_id = %s", (ev[0][0],))
    assert not audit.verify_chain(conn).ok


def test_a_forged_event_with_a_valid_looking_hash_but_wrong_link_is_detected(conn, world):
    last = conn.execute("SELECT event_id, hash FROM audit.event ORDER BY event_id DESC LIMIT 1").fetchone()
    now_id = last[0] + 1000
    h = conn.execute("SELECT audit.compute_hash(%s, now(), NULL, NULL, 'patient.read', 'x', NULL, NULL, 'success', NULL, NULL, '{}'::jsonb, NULL)",
                     (now_id,)).fetchone()[0]
    tamper(conn, "INSERT INTO audit.event (event_id, occurred_at, action, resource_type, outcome, prev_hash, hash) "
                 "VALUES (%s, now(), 'patient.read', 'x', 'success', NULL, %s)", (now_id, h))
    assert not audit.verify_chain(conn).ok


def test_reordering_events_is_detected(conn, world):
    ev = events(conn)
    a, b = ev[1][0], ev[2][0]
    tamper(conn, "UPDATE audit.event SET event_id = -1 WHERE event_id = %s", (a,))
    tamper(conn, "UPDATE audit.event SET event_id = %s WHERE event_id = %s", (a, b))
    tamper(conn, "UPDATE audit.event SET event_id = %s WHERE event_id = -1", (b,))
    assert not audit.verify_chain(conn).ok


# ------------------------------------------------------------------------------------- anchors ---
KEY = b"k" * 32


def test_an_anchor_verifies_until_history_is_truncated_or_rewritten(conn, world):
    anchor = audit.make_anchor(conn, KEY)
    assert audit.check_anchor(conn, anchor, KEY).ok
    f, u, p, e = world
    act(conn, u)
    audit.record_event(conn, "patient.read", "clinical.patient", str(p), patient_id=p)     # appending is fine
    reset(conn)
    assert audit.check_anchor(conn, anchor, KEY).ok
    with conn.transaction():                                                                # truncation of the tail
        tamper(conn, "DELETE FROM audit.event WHERE event_id = %s", (anchor["event_id"],))
        res = audit.check_anchor(conn, anchor, KEY)
        assert not res.ok
        raise psycopg.Rollback()


def test_a_complete_rewrite_with_recomputed_hashes_is_caught_by_the_anchor(conn, world):
    """The weakness of any chain: the owner rewrites history AND recomputes every hash. Only the external anchor catches it."""
    anchor = audit.make_anchor(conn, KEY)
    first = events(conn)[0][0]
    tamper(conn, "UPDATE audit.event SET outcome = 'denied' WHERE event_id = %s", (first,))
    prev = None
    for (eid,) in conn.execute("SELECT event_id FROM audit.event ORDER BY event_id").fetchall():
        conn.execute("ALTER TABLE audit.event DISABLE TRIGGER audit_event_no_update_delete")
        conn.execute("UPDATE audit.event SET prev_hash = %s, hash = audit.compute_hash(event_id, occurred_at, actor_id, actor_role, "
                     "action, resource_type, resource_id, patient_id, outcome, source_ip, request_id, details, %s) WHERE event_id = %s",
                     (prev, prev, eid))
        conn.execute("ALTER TABLE audit.event ENABLE TRIGGER audit_event_no_update_delete")
        prev = conn.execute("SELECT hash FROM audit.event WHERE event_id = %s", (eid,)).fetchone()[0]
    assert audit.verify_chain(conn).ok                       # the chain alone is fooled
    assert not audit.check_anchor(conn, anchor, KEY).ok      # the signed anchor is not


def test_a_forged_or_modified_anchor_is_rejected(conn, world):
    anchor = audit.make_anchor(conn, KEY)
    assert not audit.check_anchor(conn, anchor, b"x" * 32).ok
    forged = dict(anchor, event_id=anchor["event_id"] - 1)
    assert not audit.check_anchor(conn, forged, KEY).ok


def test_an_empty_or_broken_log_cannot_be_anchored(conn, world):
    tamper(conn, "UPDATE audit.event SET outcome = 'failure' WHERE event_id = (SELECT min(event_id) FROM audit.event)")
    with pytest.raises(RuntimeError):
        audit.make_anchor(conn, KEY)


def test_only_auditors_can_verify_through_the_application_roles(conn, world):
    f, u, p, e = world
    a = f.user("aud")
    grant(conn, a, "auditor")
    act(conn, a, "cnai_auditor")
    assert audit.verify_chain(conn).ok
    assert conn.execute("SELECT count(*) FROM audit.verify_chain()").fetchone()[0] == 0
    act(conn, u)
    denied(conn, "SELECT * FROM audit.verify_chain()")

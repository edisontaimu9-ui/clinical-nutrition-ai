"""Shared fixtures for database tests. Every test runs inside a transaction that is rolled back.

Requires DATABASE_URL pointing at a throwaway database whose name contains dev, test, ci or synthetic.
Migrations are applied (idempotently) at session start.
"""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

import migrate

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get("DATABASE_URL")


def pytest_collection_modifyitems(config, items):
    if URL:
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set; database tests need PostgreSQL (CI provides one)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def _migrated():
    if not URL:
        pytest.skip("DATABASE_URL not set")
    dbname = psycopg.conninfo.conninfo_to_dict(URL).get("dbname", "")
    assert any(t in dbname for t in ("dev", "test", "ci", "synthetic")), f"unsafe database name {dbname!r}"
    migrate.apply_migrations(URL, ROOT / "db" / "migrations")
    return URL


@pytest.fixture
def conn(_migrated):
    c = psycopg.connect(_migrated)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


class Factory:
    """Small helpers to build valid synthetic rows."""

    def __init__(self, conn):
        self.conn = conn
        self.org = uuid.uuid4()

    def user(self, name="user"):
        uid = self.conn.execute(
            "INSERT INTO iam.app_user (external_subject, display_name, org_id) VALUES (%s, %s, %s) RETURNING user_id",
            (f"synthetic-{uuid.uuid4()}", f"{name}", self.org),
        ).fetchone()[0]
        return uid

    def patient(self, sex="female"):
        return self.conn.execute(
            "INSERT INTO clinical.patient (org_id, pseudonym, sex_at_birth, is_synthetic) VALUES (%s, %s, %s, true) "
            "RETURNING patient_id",
            (self.org, f"SYN-{uuid.uuid4().hex[:8]}", sex),
        ).fetchone()[0]

    def encounter(self, patient, user, pathway="adult_general", age=480, setting="inpatient"):
        return self.conn.execute(
            "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING encounter_id",
            (patient, self.org, setting, pathway, age, user),
        ).fetchone()[0]

    def case(self, **kw):
        u = self.user("clinician")
        p = self.patient()
        e = self.encounter(p, u, **kw)
        return u, p, e

    def obs(self, enc, user, element, status="present", **kw):
        cols = {"encounter_id": enc, "element_code": element, "status": status, "recorded_by": user}
        cols.update(kw)
        names = ", ".join(cols)
        marks = ", ".join(["%s"] * len(cols))
        return self.conn.execute(
            f"INSERT INTO clinical.observation ({names}) VALUES ({marks}) RETURNING observation_id", tuple(cols.values())
        ).fetchone()[0]

    def weight(self, enc, user, kg=70, **kw):
        return self.obs(enc, user, "anthro.weight_kg", "present", value_numeric=kg, unit_entered="kg",
                        value_canonical=kg, **kw)

    def rule(self, author, rule_id="CMP-TEST", version="1"):
        self.conn.execute(
            "INSERT INTO clinical.rule (rule_id, version, kind, pathway_code, definition, author_id) "
            "VALUES (%s, %s, 'completeness', 'adult_general', '{}'::jsonb, %s)",
            (rule_id, version, author),
        )
        return rule_id, version

    def assign(self, role_key, user, ref="D-TEST"):
        self.conn.execute(
            "UPDATE ops.governance_role SET status='assigned', assigned_user_id=%s, assigned_at=now(), decision_ref=%s "
            "WHERE role_key=%s",
            (user, ref, role_key),
        )


@pytest.fixture
def f(conn):
    return Factory(conn)


def expect_violation(conn, prefix, sql, params=()):
    """Run a statement in a savepoint and assert it is rejected with a check violation carrying `prefix`."""
    with pytest.raises(psycopg.errors.CheckViolation) as info:
        with conn.transaction():
            conn.execute(sql, params)
    assert prefix in str(info.value), f"expected {prefix!r} in {info.value}"

"""Encrypted backup and restore drill (NFR-SEC-02, NFR-REL-01). Uses its own throwaway source database."""
import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import migrate
from conftest import ROOT, URL, Factory, act, grant, reset
from cnai_security import audit
from cnai_security.backup import DrillError, have_tools, run_restore_drill
from cnai_security.crypto import CryptoError, LocalKeyProvider

pytestmark = [pytest.mark.db, pytest.mark.safety,
              pytest.mark.skipif(not have_tools(), reason="pg_dump and pg_restore are required")]

TARGETS = str(ROOT / "docs" / "ops" / "recovery_targets.yaml")


@pytest.fixture
def source():
    """A committed, populated, throwaway synthetic database, dropped afterwards."""
    if not URL:
        pytest.skip("DATABASE_URL not set")
    name = f"cnai_drillsrc_test_{uuid.uuid4().hex[:8]}"
    admin_url = make_conninfo(URL, dbname="postgres")
    try:
        admin = psycopg.connect(admin_url, autocommit=True)
        admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error:
        pytest.skip("cannot create databases with this DATABASE_URL")
    url = make_conninfo(URL, dbname=name)
    try:
        migrate.apply_migrations(url, ROOT / "db" / "migrations")
        with psycopg.connect(url) as c:
            f = Factory(c)
            u = f.user("c1")
            grant(c, u, "clinician")
            act(c, u)
            p = c.execute("SELECT sec.register_patient('SYN-DRILL', 'male')").fetchone()[0]
            e = c.execute("INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                          "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s) RETURNING encounter_id", (p, f.org, u)).fetchone()[0]
            c.execute("INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                      "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e, u))
            audit.record_event(c, "patient.read", "clinical.patient", str(p), patient_id=p)
            reset(c)
            c.commit()
        yield url, name
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            cur.execute('DROP DATABASE IF EXISTS "cnai_drill_restore_test" WITH (FORCE)')
        admin.close()


def test_a_backup_is_encrypted_restored_and_matches_the_source(source, tmp_path):
    url, _ = source
    rep = run_restore_drill(url, LocalKeyProvider.generate("drill"), drill_db="cnai_drill_restore_test", targets_file=TARGETS, workdir=str(tmp_path))
    assert rep.ok, rep.problems
    assert rep.tables_compared >= 20 and rep.audit_events >= 5 and rep.encrypted_backup_bytes > 1000
    assert rep.rpo_minutes is not None and rep.seconds < 240 * 60
    assert rep.targets["status"] == "proposed"
    assert not list(tmp_path.glob("db.dump")), "the plaintext dump must not remain"
    assert b"SYN-DRILL" not in (tmp_path / "db.dump.enc").read_bytes()


def test_the_restored_database_is_dropped_after_the_drill(source, tmp_path):
    url, _ = source
    run_restore_drill(url, LocalKeyProvider.generate("drill"), drill_db="cnai_drill_restore_test", workdir=str(tmp_path))
    with psycopg.connect(make_conninfo(URL, dbname="postgres")) as c:
        assert c.execute("SELECT count(*) FROM pg_database WHERE datname = 'cnai_drill_restore_test'").fetchone()[0] == 0


def test_the_drill_refuses_unsafe_database_names(source, tmp_path):
    url, name = source
    with pytest.raises(DrillError):
        run_restore_drill(url, LocalKeyProvider.generate("d"), drill_db="production", workdir=str(tmp_path))
    with pytest.raises(DrillError):
        run_restore_drill(make_conninfo(url, dbname="customers"), LocalKeyProvider.generate("d"), drill_db="cnai_drill_restore_test", workdir=str(tmp_path))
    with pytest.raises(DrillError):
        run_restore_drill(url, LocalKeyProvider.generate("d"), drill_db=name, workdir=str(tmp_path))


def test_a_drill_that_loses_data_is_reported_as_failed(source, tmp_path, monkeypatch):
    """Prove the comparison has teeth: break the restore and the report must say so."""
    import cnai_security.backup as b
    real = b.subprocess.run

    def sabotage(cmd, *a, **k):
        r = real(cmd, *a, **k)
        if cmd[0] == "pg_restore":
            with psycopg.connect(cmd[cmd.index("--dbname") + 1]) as c:
                c.execute("ALTER TABLE audit.event DISABLE TRIGGER audit_event_no_update_delete")
                c.execute("DELETE FROM audit.event WHERE event_id = (SELECT max(event_id) FROM audit.event)")
        return r

    monkeypatch.setattr(b.subprocess, "run", sabotage)
    rep = run_restore_drill(source[0], LocalKeyProvider.generate("drill"), drill_db="cnai_drill_restore_test", workdir=str(tmp_path))
    assert not rep.ok and any("row counts differ" in p for p in rep.problems)


def test_a_backup_cannot_be_restored_with_the_wrong_key(source, tmp_path):
    from cnai_security.crypto import decrypt_file
    run_restore_drill(source[0], LocalKeyProvider.generate("drill"), drill_db="cnai_drill_restore_test", workdir=str(tmp_path))
    with pytest.raises(CryptoError):
        decrypt_file(LocalKeyProvider.generate("drill"), str(tmp_path / "db.dump.enc"), str(tmp_path / "x"), context=b"backup:" + source[1].encode())

"""Row-level security, least-privilege roles and break-glass access (FR-ADM-01, NFR-SEC-03, FR-AUD-01).

Every test builds a small synthetic world as the setup (owner) role, then acts through the application roles.
"""
import psycopg
import pytest

from conftest import Factory, act, denied, grant, reset

pytestmark = [pytest.mark.db, pytest.mark.safety]
RLS = psycopg.errors.InsufficientPrivilege  # raised for both missing privileges and row-security violations


class World:
    """Org A: two clinicians (c1, c2), a supervisor, a safety lead, an admin, an auditor user. Org B: one clinician."""

    def __init__(self, conn):
        self.conn = conn
        a, b = Factory(conn), Factory(conn)
        self.a, self.b = a, b
        self.c1, self.c2, self.sup = a.user("c1"), a.user("c2"), a.user("sup")
        self.lead, self.admin, self.aud = a.user("lead"), a.user("admin"), a.user("auditor")
        self.cb = b.user("cb")
        for u, r in ((self.c1, "clinician"), (self.c2, "clinician"), (self.sup, "supervisor"), (self.lead, "clinical_safety_lead"),
                     (self.admin, "admin"), (self.aud, "auditor"), (self.cb, "clinician")):
            grant(conn, u, r)
        act(conn, self.c1)
        self.p1 = conn.execute("SELECT sec.register_patient('SYN-P1', 'female')").fetchone()[0]
        act(conn, self.c2)
        self.p2 = conn.execute("SELECT sec.register_patient('SYN-P2', 'male')").fetchone()[0]
        act(conn, self.cb)
        self.pb = conn.execute("SELECT sec.register_patient('SYN-PB', 'male')").fetchone()[0]
        reset(conn)

    def encounter(self, user, patient, org):
        act(self.conn, user)
        e = self.conn.execute(
            "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
            "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s) RETURNING encounter_id", (patient, org, user)).fetchone()[0]
        reset(self.conn)
        return e


@pytest.fixture
def w(conn):
    return World(conn)


def pseudonyms(conn):
    return sorted(r[0] for r in conn.execute("SELECT pseudonym FROM clinical.patient").fetchall())


# ------------------------------------------------------------------ cross-patient and cross-facility ---
def test_a_clinician_sees_only_patients_in_their_own_care(conn, w):
    act(conn, w.c1)
    assert pseudonyms(conn) == ["SYN-P1"]            # not SYN-P2 (same organisation), not SYN-PB (other organisation)
    act(conn, w.cb)
    assert pseudonyms(conn) == ["SYN-PB"]


def test_no_context_or_an_unknown_user_sees_nothing(conn, w):
    import uuid
    act(conn, None)
    assert pseudonyms(conn) == []
    act(conn, uuid.uuid4())
    assert pseudonyms(conn) == []


def test_a_client_supplied_organisation_changes_nothing(conn, w):
    act(conn, w.c1)
    conn.execute("SELECT set_config('app.org_id', %s, true)", (str(w.b.org),))
    assert pseudonyms(conn) == ["SYN-P1"]
    assert conn.execute("SELECT sec.app_org()").fetchone()[0] == w.a.org


def test_an_inactive_user_sees_nothing(conn, w):
    reset(conn)
    conn.execute("UPDATE iam.app_user SET is_active = false WHERE user_id = %s", (w.c1,))
    act(conn, w.c1)
    assert pseudonyms(conn) == []


def test_an_expired_role_grant_removes_access(conn, w):
    reset(conn)
    conn.execute("UPDATE iam.role_grant SET expires_at = now() - interval '1 minute' WHERE user_id = %s", (w.c1,))
    act(conn, w.c1)
    assert pseudonyms(conn) == []


def test_a_user_without_a_clinical_role_sees_nothing_even_with_a_care_relationship(conn, w):
    reset(conn)
    conn.execute("DELETE FROM iam.role_grant WHERE user_id = %s", (w.c1,))
    grant(conn, w.c1, "engineer")
    act(conn, w.c1)
    assert pseudonyms(conn) == []


def test_clinicians_cannot_create_patients_or_care_relationships_directly(conn, w):
    act(conn, w.c1)
    denied(conn, "INSERT INTO clinical.patient (org_id, pseudonym, is_synthetic) VALUES (%s, 'SYN-X', true)", (w.a.org,))
    denied(conn, "INSERT INTO clinical.care_relationship (user_id, patient_id) VALUES (%s, %s)", (w.c1, w.p2))


def test_a_clinician_cannot_register_a_patient_without_a_clinician_grant(conn, w):
    act(conn, w.admin, "cnai_clinician")
    denied(conn, "SELECT sec.register_patient('SYN-Y', 'male')")


def test_writes_to_another_clinicians_patient_are_refused(conn, w):
    e2 = w.encounter(w.c2, w.p2, w.a.org)
    act(conn, w.c1)
    denied(conn, "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                 "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s)", (w.p2, w.a.org, w.c1))
    denied(conn, "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e2, w.c1))


def test_an_encounter_cannot_be_attributed_to_someone_else_or_another_organisation(conn, w):
    act(conn, w.c1)
    denied(conn, "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                 "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s)", (w.p1, w.a.org, w.c2))
    denied(conn, "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                 "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s)", (w.p1, w.b.org, w.c1))


def test_observations_are_readable_and_writable_only_through_own_encounters(conn, w):
    e1 = w.encounter(w.c1, w.p1, w.a.org)
    act(conn, w.c1)
    conn.execute("INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e1, w.c1))
    assert conn.execute("SELECT count(*) FROM clinical.observation").fetchone()[0] == 1
    denied(conn, "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 71, 'kg', 71, %s)", (e1, w.c2))   # recorded_by must be the caller
    act(conn, w.c2)
    assert conn.execute("SELECT count(*) FROM clinical.observation").fetchone()[0] == 0
    act(conn, w.cb)
    assert conn.execute("SELECT count(*) FROM clinical.observation").fetchone()[0] == 0


def test_observations_stay_append_only_for_app_roles(conn, w):
    e1 = w.encounter(w.c1, w.p1, w.a.org)
    act(conn, w.c1)
    conn.execute("INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e1, w.c1))
    denied(conn, "UPDATE clinical.observation SET value_numeric = 1")
    denied(conn, "DELETE FROM clinical.observation")


def test_grant_care_shares_a_patient_with_a_colleague_but_only_within_the_organisation(conn, w):
    act(conn, w.c1)
    conn.execute("SELECT sec.grant_care(%s, %s)", (w.p1, w.c2))
    act(conn, w.c2)
    assert pseudonyms(conn) == ["SYN-P1", "SYN-P2"]
    act(conn, w.c1)
    denied(conn, "SELECT sec.grant_care(%s, %s)", (w.p1, w.cb))        # other organisation
    denied(conn, "SELECT sec.grant_care(%s, %s)", (w.p2, w.c1))        # no care relationship with p2
    denied(conn, "SELECT sec.grant_care(%s, %s)", (w.p1, w.admin))     # target is not a clinician


def test_an_ended_care_relationship_removes_access(conn, w):
    reset(conn)
    conn.execute("UPDATE clinical.care_relationship SET ends_at = now() - interval '1 second' WHERE user_id = %s", (w.c1,))
    act(conn, w.c1)
    assert pseudonyms(conn) == []


# ------------------------------------------------------------------ admin, auditor, identity vault ---
def test_admin_has_no_access_to_patient_content(conn, w):
    act(conn, w.admin, "cnai_admin")
    for table in ("clinical.patient", "clinical.encounter", "clinical.observation", "clinical.finding",
                  "clinical.calculation", "identity.patient_identity"):
        denied(conn, f"SELECT * FROM {table}")


def test_admin_manages_users_in_own_organisation_only(conn, w):
    act(conn, w.admin, "cnai_admin")
    names = sorted(r[0] for r in conn.execute("SELECT display_name FROM iam.app_user").fetchall())
    assert "cb" not in names and "c1" in names
    conn.execute("INSERT INTO iam.app_user (external_subject, display_name, org_id) VALUES ('syn-new', 'new', %s)", (w.a.org,))
    denied(conn, "INSERT INTO iam.app_user (external_subject, display_name, org_id) VALUES ('syn-new2', 'x', %s)", (w.b.org,))
    conn.execute("INSERT INTO iam.role_grant (user_id, role, granted_by) VALUES (%s, 'clinician', %s)", (w.aud, w.admin))
    denied(conn, "INSERT INTO iam.role_grant (user_id, role, granted_by) VALUES (%s, 'clinician', %s)", (w.cb, w.admin))


def test_admin_cannot_grant_roles_to_themselves_or_forge_the_grantor(conn, w):
    act(conn, w.admin, "cnai_admin")
    denied(conn, "INSERT INTO iam.role_grant (user_id, role, granted_by) VALUES (%s, 'clinician', %s)", (w.admin, w.admin))
    denied(conn, "INSERT INTO iam.role_grant (user_id, role, granted_by) VALUES (%s, 'supervisor', %s)", (w.aud, w.c1))
    # an UPDATE that RLS filters out changes no rows (it is not an error), and the admin's own grant is untouched
    cur = conn.execute("UPDATE iam.role_grant SET expires_at = NULL WHERE user_id = %s", (w.admin,))
    assert cur.rowcount == 0
    cur = conn.execute("UPDATE iam.role_grant SET expires_at = NULL WHERE user_id = %s", (w.cb,))   # other organisation
    assert cur.rowcount == 0


def test_admin_cannot_appoint_themselves_to_a_governance_role(conn, w):
    act(conn, w.admin, "cnai_admin")
    denied(conn, "UPDATE ops.governance_role SET status = 'assigned', assigned_user_id = %s, assigned_at = now(), decision_ref = 'D-TEST' "
                 "WHERE role_key = 'independent_clinical_safety_reviewer'", (w.admin,))
    # appointing someone else is allowed (the governance triggers still apply)
    conn.execute("UPDATE ops.governance_role SET status = 'assigned', assigned_user_id = %s, assigned_at = now(), decision_ref = 'D-TEST' "
                 "WHERE role_key = 'independent_clinical_safety_reviewer'", (w.c2,))


def test_auditor_reads_audit_metadata_but_not_patient_content(conn, w):
    act(conn, w.aud, "cnai_auditor")
    assert conn.execute("SELECT count(*) FROM audit.event").fetchone()[0] > 0
    for table in ("clinical.patient", "clinical.observation", "identity.patient_identity", "iam.role_grant"):
        denied(conn, f"SELECT * FROM {table}")
    denied(conn, "INSERT INTO audit.event (event_id, occurred_at, action, resource_type, outcome, hash) "
                 "VALUES (999999, now(), 'x.y', 'x', 'success', '\\x00')")


def test_the_identity_vault_is_closed_to_clinicians_and_scoped_for_the_identity_service(conn, w):
    act(conn, w.c1)
    denied(conn, "SELECT * FROM identity.patient_identity")
    act(conn, w.c1, "cnai_identity_service")
    conn.execute("INSERT INTO identity.patient_identity (patient_id, org_id, encrypted_identifiers, key_id) VALUES (%s, %s, '\\x01', 'k1')",
                 (w.p1, w.a.org))
    assert conn.execute("SELECT count(*) FROM identity.patient_identity").fetchone()[0] == 1
    denied(conn, "INSERT INTO identity.patient_identity (patient_id, org_id, encrypted_identifiers, key_id) VALUES (%s, %s, '\\x01', 'k1')",
           (w.p2, w.a.org))                                  # not c1's patient
    act(conn, w.c2, "cnai_identity_service")
    assert conn.execute("SELECT count(*) FROM identity.patient_identity").fetchone()[0] == 0


# ------------------------------------------------------------------ break-glass ---
def test_safety_lead_has_no_patient_access_until_a_second_person_approves_break_glass(conn, w):
    act(conn, w.lead, "cnai_safety_lead")
    assert pseudonyms(conn) == []
    act(conn, w.sup)
    gid = conn.execute("SELECT sec.grant_break_glass(%s, %s, 'Incident review of an adverse nutrition event', 4)", (w.lead, w.p1)).fetchone()[0]
    assert gid
    act(conn, w.lead, "cnai_safety_lead")
    assert pseudonyms(conn) == ["SYN-P1"]                    # only the named patient


def test_break_glass_is_read_only(conn, w):
    e1 = w.encounter(w.c1, w.p1, w.a.org)
    act(conn, w.sup)
    conn.execute("SELECT sec.grant_break_glass(%s, %s, 'Incident review of an adverse nutrition event', 2)", (w.lead, w.p1))
    act(conn, w.lead, "cnai_safety_lead")
    assert conn.execute("SELECT count(*) FROM clinical.encounter").fetchone()[0] == 1
    denied(conn, "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e1, w.lead))


def test_break_glass_rules(conn, w):
    act(conn, w.sup)
    just = "Incident review of an adverse nutrition event"
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, %s, 4)", (w.sup, w.p1, just), (RLS,))              # self-approval
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, %s, 25)", (w.lead, w.p1, just), (psycopg.errors.CheckViolation,))  # too long
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, 'too short', 4)", (w.lead, w.p1), (psycopg.errors.CheckViolation,))
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, %s, 4)", (w.cb, w.p1, just), (RLS,))               # beneficiary in another org
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, %s, 4)", (w.lead, w.pb, just), (RLS,))             # patient in another org
    act(conn, w.c2)
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, %s, 4)", (w.lead, w.p1, just), (RLS,))             # approver is not a supervisor/lead
    act(conn, w.cb)
    denied(conn, "SELECT sec.grant_break_glass(%s, %s, %s, 4)", (w.c1, w.p1, just), (RLS,))               # approver in another org


def test_an_expired_break_glass_grant_gives_no_access(conn, w):
    reset(conn)
    conn.execute("INSERT INTO ops.break_glass (user_id, patient_id, justification, approved_by, starts_at, expires_at) "
                 "VALUES (%s, %s, 'Incident review of an adverse nutrition event', %s, now() - interval '3 hours', now() - interval '1 hour')",
                 (w.lead, w.p1, w.sup))
    act(conn, w.lead, "cnai_safety_lead")
    assert pseudonyms(conn) == []


def test_break_glass_grants_cannot_be_edited_or_deleted(conn, w):
    act(conn, w.sup)
    conn.execute("SELECT sec.grant_break_glass(%s, %s, 'Incident review of an adverse nutrition event', 2)", (w.lead, w.p1))
    reset(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE ops.break_glass SET expires_at = expires_at + interval '1 hour'")
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("DELETE FROM ops.break_glass")


def test_break_glass_is_audited_with_both_people(conn, w):
    act(conn, w.sup)
    conn.execute("SELECT sec.grant_break_glass(%s, %s, 'Incident review of an adverse nutrition event', 2)", (w.lead, w.p1))
    reset(conn)
    row = conn.execute("SELECT actor_id, details->>'user_id', details->>'approved_by', patient_id FROM audit.event "
                       "WHERE action = 'ops.break_glass.insert'").fetchone()
    assert row == (w.sup, str(w.lead), str(w.sup), None) or row[:3] == (w.sup, str(w.lead), str(w.sup))


# ------------------------------------------------------------------ the roles themselves ---
APP_ROLES = ("cnai_clinician", "cnai_safety_lead", "cnai_admin", "cnai_auditor", "cnai_identity_service", "cnai_auth_service")


def test_application_roles_are_not_superusers_owners_or_rls_bypassers(conn):
    rows = conn.execute("SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolcreaterole, rolcreatedb FROM pg_roles "
                        "WHERE rolname = ANY(%s)", (list(APP_ROLES),)).fetchall()
    assert {r[0] for r in rows} == set(APP_ROLES)
    for name, sup, bypass, login, createrole, createdb in rows:
        assert not (sup or bypass or login or createrole or createdb), name
    owned = conn.execute("SELECT c.relname, r.rolname FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                         "WHERE r.rolname = ANY(%s)", (list(APP_ROLES),)).fetchall()
    assert owned == []


def test_row_level_security_is_enabled_on_every_patient_related_table(conn):
    must = {"clinical.patient", "clinical.encounter", "clinical.observation", "clinical.calculation", "clinical.finding",
            "clinical.care_relationship", "identity.patient_identity", "iam.app_user", "iam.role_grant",
            "iam.cosign_relationship", "ops.governance_role", "ops.instance_config", "ops.break_glass"}
    on = {f"{r[0]}.{r[1]}" for r in conn.execute(
        "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relrowsecurity").fetchall()}
    assert must <= on, must - on


def test_every_clinical_table_with_a_patient_or_encounter_key_has_row_security(conn):
    """A future table that references a patient or encounter without RLS fails here (guards against forgetting)."""
    rows = conn.execute("""
        SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname IN ('patient_id', 'encounter_id') AND NOT a.attisdropped
        WHERE c.relkind = 'r' AND n.nspname IN ('clinical', 'identity', 'ops') AND NOT c.relrowsecurity""").fetchall()
    assert rows == []


def test_public_has_no_privileges_on_schemas_or_tables(conn):
    leaks = conn.execute("""
        SELECT n.nspname FROM pg_namespace n WHERE n.nspname IN ('iam','identity','clinical','ops','audit','sec')
          AND has_schema_privilege('public', n.oid, 'USAGE')""").fetchall()
    assert leaks == []
    t = conn.execute("""
        SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' AND n.nspname IN ('iam','identity','clinical','ops','audit')
          AND (has_table_privilege('public', c.oid, 'SELECT') OR has_table_privilege('public', c.oid, 'INSERT'))""").fetchall()
    assert t == []


# ------------------------------------------------------------------ defence in depth (each guard alone must hold) ---
def test_break_glass_cannot_write_even_through_a_role_that_may_write(conn, w):
    """A supervisor holding only break-glass access (no care relationship) can read the patient but not write: the
    row-level policy refuses it, independently of table privileges."""
    e1 = w.encounter(w.c1, w.p1, w.a.org)
    act(conn, w.lead, "cnai_safety_lead")
    conn.execute("SELECT sec.grant_break_glass(%s, %s, 'Incident review of an adverse nutrition event', 2)", (w.sup, w.p1))
    act(conn, w.sup)                                              # clinician database role: has INSERT privilege
    assert pseudonyms(conn) == ["SYN-P1"]
    assert conn.execute("SELECT count(*) FROM clinical.encounter WHERE patient_id = %s", (w.p1,)).fetchone()[0] == 1
    denied(conn, "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e1, w.sup))
    denied(conn, "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                 "VALUES (%s, %s, 'inpatient', 'adult_general', 480, %s)", (w.p1, w.a.org, w.sup))
    denied(conn, "SELECT sec.grant_care(%s, %s)", (w.p1, w.c2))


def test_an_encounter_stamped_with_another_organisation_exposes_nothing_beneath_it(conn, w):
    """Observations are reachable only through an encounter of the caller's own organisation, even if the patient matches."""
    e1 = w.encounter(w.c1, w.p1, w.a.org)
    reset(conn)
    conn.execute("INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, value_canonical, recorded_by) "
                 "VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s)", (e1, w.c1))
    act(conn, w.c1)
    assert conn.execute("SELECT count(*) FROM clinical.observation").fetchone()[0] == 1
    reset(conn)
    conn.execute("ALTER TABLE clinical.encounter DISABLE TRIGGER USER")          # owner-only: simulate inconsistent data
    conn.execute("UPDATE clinical.encounter SET org_id = %s WHERE encounter_id = %s", (w.b.org, e1))
    act(conn, w.c1)
    assert conn.execute("SELECT count(*) FROM clinical.observation").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM clinical.encounter").fetchone()[0] == 0


def test_inactive_users_hold_no_roles_at_the_function_level(conn, w):
    reset(conn)
    conn.execute("UPDATE iam.app_user SET is_active = false WHERE user_id = %s", (w.c1,))
    act(conn, w.c1)
    assert conn.execute("SELECT sec.has_role(ARRAY['clinician'])").fetchone()[0] is False
    assert conn.execute("SELECT sec.app_org()").fetchone()[0] is None
    act(conn, w.c2)
    assert conn.execute("SELECT sec.has_role(ARRAY['clinician'])").fetchone()[0] is True

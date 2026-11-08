"""Database tests for population pathways (FR-POP-01..04), data-class guard (D-020) and governance (FR-GOV-01/02, D-019)."""
import psycopg
import pytest

from conftest import expect_violation

pytestmark = [pytest.mark.db, pytest.mark.safety]


# ---------------------------------------------------------------- pathways ---
def test_only_the_adult_pathway_is_active(conn):
    rows = dict(conn.execute("SELECT pathway_code, status FROM clinical.pathway").fetchall())
    assert rows == {"adult_general": "active", "paediatric_general": "planned", "paediatric_sam": "planned"}


@pytest.mark.parametrize("pathway,age", [
    ("paediatric_general", 60),     # planned pathway, correct age
    ("paediatric_sam", 24),         # planned pathway, correct age
    ("paediatric_sam", 480),        # planned pathway, wrong age
])
def test_planned_pathways_are_refused(f, conn, pathway, age):
    u = f.user()
    p = f.patient()
    expect_violation(conn, "CNAI-POP",
                     "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                     "VALUES (%s, %s, 'inpatient', %s, %s, %s)", (p, f.org, pathway, age, u))


@pytest.mark.parametrize("age", [0, 6, 59, 120, 215])
def test_children_are_refused_on_the_adult_pathway(f, conn, age):
    u = f.user()
    p = f.patient()
    expect_violation(conn, "CNAI-POP",
                     "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                     "VALUES (%s, %s, 'inpatient', 'adult_general', %s, %s)", (p, f.org, age, u))


@pytest.mark.parametrize("age", [216, 217, 480, 1200])
def test_adults_are_accepted_on_the_adult_pathway(f, age):
    f.case(age=age)


def test_age_boundary_is_exactly_18_years(f, conn):
    u = f.user()
    p = f.patient()
    f.encounter(p, u, age=216)
    expect_violation(conn, "CNAI-POP",
                     "INSERT INTO clinical.encounter (patient_id, org_id, setting, pathway, age_months_at_encounter, created_by) "
                     "VALUES (%s, %s, 'inpatient', 'adult_general', 215, %s)", (p, f.org, u))


def test_unknown_pathway_is_refused(f, conn):
    u = f.user()
    p = f.patient()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            f.encounter(p, u, pathway="made_up")


def test_a_pathway_cannot_be_activated_without_a_decision_reference(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE clinical.pathway SET status='active' WHERE pathway_code='paediatric_sam'")


def test_pathways_are_never_deleted(conn):
    expect_violation(conn, "CNAI-HIST", "DELETE FROM clinical.pathway WHERE pathway_code='paediatric_sam'")


# ------------------------------------------------- identity separation ---
def test_clinical_tables_hold_no_direct_identifier_columns(conn):
    banned = ("name", "surname", "phone", "email", "address", "dob", "birth", "national_id", "nrc", "passport", "ssn")
    # Patient-level tables only; reference data (standards, pathways, catalogue, rules) legitimately has names/labels.
    reference_tables = ("reference_standard", "reference_lms", "pathway", "data_element_def", "rule", "rule_review")
    cols = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='clinical' "
        "AND table_name <> ALL (%s)", (list(reference_tables),)).fetchall()
    assert {t for t, _ in cols} >= {"patient", "encounter", "observation", "calculation", "finding"}
    hits = [(t, c) for t, c in cols if any(b in c.lower() for b in banned) and c != "sex_at_birth"]
    assert hits == [], f"possible identifier columns in clinical schema: {hits}"


# ------------------------------------------------------ data-class guard ---
def test_instance_is_synthetic_development_by_default(conn):
    assert conn.execute("SELECT data_class, mode FROM ops.instance_config").fetchone() == ("synthetic", "development")


def test_a_real_patient_cannot_be_created_in_a_synthetic_instance(f, conn):
    expect_violation(conn, "CNAI-DATA",
                     "INSERT INTO clinical.patient (org_id, pseudonym, is_synthetic) VALUES (%s, 'REAL-1', false)", (f.org,))


def test_synthetic_flag_is_immutable(f, conn):
    p = f.patient()
    expect_violation(conn, "CNAI-DATA", "UPDATE clinical.patient SET is_synthetic=false WHERE patient_id=%s", (p,))


def test_real_data_class_is_impossible_in_development_mode(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE ops.instance_config SET data_class='real'")


def test_data_class_cannot_change_while_patients_exist(f, conn):
    f.patient()
    expect_violation(conn, "CNAI-DATA", "UPDATE ops.instance_config SET data_class='real', mode='shadow'")


def test_instance_config_cannot_be_deleted_or_duplicated(conn):
    expect_violation(conn, "CNAI-DATA", "DELETE FROM ops.instance_config")
    with pytest.raises(psycopg.errors.Error):
        with conn.transaction():
            conn.execute("INSERT INTO ops.instance_config (singleton, data_class, mode) VALUES (true, 'synthetic', 'development')")


# ------------------------------------------------------------ governance ---
def test_governance_roles_start_vacant_and_nothing_is_hard_coded_to_a_person(conn):
    rows = conn.execute("SELECT role_key, status, assigned_user_id FROM ops.governance_role").fetchall()
    assert {r[0] for r in rows} == {"product_clinical_requirements_lead", "independent_clinical_safety_reviewer",
                                    "privacy_security_lead"}
    assert all(r[1] == "vacant" and r[2] is None for r in rows)


def test_an_appointment_needs_a_decision_reference(f, conn):
    u = f.user()
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE ops.governance_role SET status='assigned', assigned_user_id=%s "
                         "WHERE role_key='independent_clinical_safety_reviewer'", (u,))


def test_independent_reviewer_must_differ_from_the_product_lead(f, conn):
    u = f.user()
    f.assign("product_clinical_requirements_lead", u)
    expect_violation(conn, "CNAI-GOV",
                     "UPDATE ops.governance_role SET status='assigned', assigned_user_id=%s, decision_ref='D-X' "
                     "WHERE role_key='independent_clinical_safety_reviewer'", (u,))


def test_rules_start_as_draft_and_cannot_be_inserted_approved(f, conn):
    lead = f.user("lead")
    expect_violation(conn, "CNAI-GOV",
                     "INSERT INTO clinical.rule (rule_id, version, kind, pathway_code, definition, author_id, review_status) "
                     "VALUES ('R1', '1', 'completeness', 'adult_general', '{}', %s, 'independently_reviewed')", (lead,))


def test_review_status_cannot_be_edited_directly(f, conn):
    author = f.user("author")
    rid, ver = f.rule(author)
    expect_violation(conn, "CNAI-GOV",
                     "UPDATE clinical.rule SET review_status='independently_reviewed' WHERE rule_id=%s AND version=%s", (rid, ver))


def test_rule_content_is_immutable_and_rules_are_not_deleted(f, conn):
    author = f.user("author")
    rid, ver = f.rule(author)
    expect_violation(conn, "CNAI-HIST", "UPDATE clinical.rule SET definition='{\"x\":1}' WHERE rule_id=%s", (rid,))
    expect_violation(conn, "CNAI-HIST", "DELETE FROM clinical.rule WHERE rule_id=%s", (rid,))


def test_author_approval_requires_the_assigned_product_lead(f, conn):
    author = f.user("author")
    stranger = f.user("stranger")
    rid, ver = f.rule(author)
    expect_violation(conn, "CNAI-GOV",
                     "INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                     "VALUES (%s, %s, 'author_approved', %s, 'approved')", (rid, ver, stranger))
    f.assign("product_clinical_requirements_lead", author)
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                 "VALUES (%s, %s, 'author_approved', %s, 'approved')", (rid, ver, author))
    assert conn.execute("SELECT review_status FROM clinical.rule WHERE rule_id=%s", (rid,)).fetchone()[0] == "author_approved"


def test_independent_review_is_impossible_while_the_reviewer_role_is_vacant(f, conn):
    lead = f.user("lead")
    f.assign("product_clinical_requirements_lead", lead)
    rid, ver = f.rule(lead)
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                 "VALUES (%s, %s, 'author_approved', %s, 'approved')", (rid, ver, lead))
    expect_violation(conn, "CNAI-GOV",
                     "INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                     "VALUES (%s, %s, 'independently_reviewed', %s, 'approved')", (rid, ver, lead))


def test_the_full_review_ladder_with_an_independent_reviewer(f, conn):
    lead, reviewer = f.user("lead"), f.user("reviewer")
    f.assign("product_clinical_requirements_lead", lead)
    f.assign("independent_clinical_safety_reviewer", reviewer)
    rid, ver = f.rule(lead)
    status = lambda: conn.execute("SELECT review_status FROM clinical.rule WHERE rule_id=%s", (rid,)).fetchone()[0]
    assert status() == "draft"
    # cannot skip author approval
    expect_violation(conn, "CNAI-GOV",
                     "INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                     "VALUES (%s, %s, 'independently_reviewed', %s, 'approved')", (rid, ver, reviewer))
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                 "VALUES (%s, %s, 'author_approved', %s, 'approved')", (rid, ver, lead))
    assert status() == "author_approved"
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                 "VALUES (%s, %s, 'independently_reviewed', %s, 'approved')", (rid, ver, reviewer))
    assert status() == "independently_reviewed"
    # a later change request returns the rule to draft
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome, comment) "
                 "VALUES (%s, %s, 'independently_reviewed', %s, 'changes_requested', 'threshold unclear')", (rid, ver, reviewer))
    assert status() == "draft"


def test_an_author_cannot_independently_review_their_own_rule(f, conn):
    """Even if the same person were somehow assigned both roles, the author check holds (defence in depth)."""
    lead, reviewer = f.user("lead"), f.user("reviewer")
    f.assign("product_clinical_requirements_lead", lead)
    f.assign("independent_clinical_safety_reviewer", reviewer)
    rid, ver = f.rule(reviewer)                       # the reviewer authored this rule
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                 "VALUES (%s, %s, 'author_approved', %s, 'approved')", (rid, ver, lead))
    expect_violation(conn, "CNAI-GOV",
                     "INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                     "VALUES (%s, %s, 'independently_reviewed', %s, 'approved')", (rid, ver, reviewer))


def test_reviews_are_append_only(f, conn):
    lead = f.user("lead")
    f.assign("product_clinical_requirements_lead", lead)
    rid, ver = f.rule(lead)
    conn.execute("INSERT INTO clinical.rule_review (rule_id, version, level, reviewer_id, outcome) "
                 "VALUES (%s, %s, 'author_approved', %s, 'approved')", (rid, ver, lead))
    expect_violation(conn, "CNAI-HIST", "UPDATE clinical.rule_review SET outcome='changes_requested'")
    expect_violation(conn, "CNAI-HIST", "DELETE FROM clinical.rule_review")


def test_migration_runner_refuses_a_changed_applied_migration(tmp_path, _migrated):
    import migrate
    (tmp_path / "0001_x.sql").write_text("SELECT 1;")
    # use a scratch checksum table row to simulate; the real runner stores sha256 per name
    with psycopg.connect(_migrated, autocommit=True) as c:
        c.execute("DELETE FROM public.schema_migrations WHERE name='0001_x.sql'")
    migrate.apply_migrations(_migrated, tmp_path)
    (tmp_path / "0001_x.sql").write_text("SELECT 2;")
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        migrate.apply_migrations(_migrated, tmp_path)
    with psycopg.connect(_migrated, autocommit=True) as c:
        c.execute("DELETE FROM public.schema_migrations WHERE name='0001_x.sql'")

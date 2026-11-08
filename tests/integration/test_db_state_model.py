"""Database tests for the five-state data-element model (D-003, FR-STATE-01/02, FR-INT-05, FR-POP-03).

Covers exit criterion: "DB constraint tests prove invalid state/value combinations are rejected."
"""
import itertools

import psycopg
import pytest

pytestmark = [pytest.mark.db, pytest.mark.safety]

STATUSES = ["present", "not_assessed", "not_applicable", "unknown_or_unreliable", "declared_normal_by_clinician"]


def test_absence_defaults_to_not_assessed(f, conn):
    u, _, e = f.case()
    oid = f.obs(e, u, "clin.oedema_present", status="not_assessed")
    status = conn.execute("SELECT status FROM clinical.observation WHERE observation_id=%s", (oid,)).fetchone()[0]
    assert status == "not_assessed"
    # the column default is also not_assessed, never a value or 'normal'
    default = conn.execute(
        "SELECT column_default FROM information_schema.columns WHERE table_schema='clinical' AND table_name='observation' "
        "AND column_name='status'").fetchone()[0]
    assert "not_assessed" in default


@pytest.mark.parametrize("status,has_value", list(itertools.product(STATUSES, [True, False])))
def test_value_allowed_only_when_present(f, conn, status, has_value):
    u, _, e = f.case()
    kw = {"declared_by": u} if status == "declared_normal_by_clinician" else {}
    if has_value:
        kw.update(value_numeric=70, unit_entered="kg", value_canonical=70)
    should_pass = (status == "present") == has_value
    if should_pass:
        f.obs(e, u, "anthro.weight_kg", status=status, **kw)
    else:
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                f.obs(e, u, "anthro.weight_kg", status=status, **kw)


def test_unit_may_not_hide_on_a_non_present_observation(f, conn):
    u, _, e = f.case()
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            f.obs(e, u, "anthro.weight_kg", status="not_assessed", unit_entered="kg")


def test_declared_normal_requires_a_named_clinician(f, conn):
    u, _, e = f.case()
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            f.obs(e, u, "clin.oedema_present", status="declared_normal_by_clinician")
    f.obs(e, u, "clin.oedema_present", status="declared_normal_by_clinician", declared_by=u)


def test_proposed_from_note_cannot_be_confirmed_without_a_clinician(f, conn):
    u, _, e = f.case()
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            f.obs(e, u, "clin.gi_symptoms", value_text="nausea", source="proposed_from_note", confirmed=True)
    f.obs(e, u, "clin.gi_symptoms", value_text="nausea", source="proposed_from_note", confirmed=False)
    f.obs(e, u, "clin.gi_symptoms", value_text="nausea", source="proposed_from_note", confirmed=True, confirmed_by=u)


def test_numeric_value_requires_entered_unit_and_canonical_value(f, conn, ):
    from conftest import expect_violation
    u, _, e = f.case()
    expect_violation(conn, "CNAI-STATE",
                     "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, recorded_by) "
                     "VALUES (%s, 'anthro.weight_kg', 'present', 70, %s)", (e, u))


@pytest.mark.parametrize("element,kwargs", [
    ("anthro.weight_kg", {"value_text": "heavy"}),             # text where numeric required
    ("clin.oedema_present", {"value_numeric": 1}),             # numeric where boolean required
    ("clin.gi_symptoms", {"value_bool": True}),                # boolean where text required
])
def test_value_type_must_match_element(f, conn, element, kwargs):
    u, _, e = f.case()
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            f.obs(e, u, element, **kwargs)


def test_coded_value_must_be_in_allowed_list(f, conn):
    u, _, e = f.case()
    f.obs(e, u, "intake.appetite", value_text="reduced")
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            f.obs(e, u, "intake.appetite", value_text="fine")


def test_observations_are_append_only(f, conn):
    from conftest import expect_violation
    u, _, e = f.case()
    oid = f.weight(e, u)
    expect_violation(conn, "CNAI-HIST", "UPDATE clinical.observation SET value_numeric = 99 WHERE observation_id = %s", (oid,))
    expect_violation(conn, "CNAI-HIST", "DELETE FROM clinical.observation WHERE observation_id = %s", (oid,))
    expect_violation(conn, "CNAI-HIST", "TRUNCATE clinical.observation")


def test_a_correction_is_a_new_version_that_supersedes_once(f, conn):
    u, _, e = f.case()
    first = f.weight(e, u, kg=70)
    second = f.weight(e, u, kg=71, supersedes_id=first)
    assert second != first
    with pytest.raises(psycopg.errors.UniqueViolation):
        with conn.transaction():
            f.weight(e, u, kg=72, supersedes_id=first)


def test_a_correction_must_target_the_same_element_and_encounter(f, conn):
    u, p, e = f.case()
    other_encounter = f.encounter(p, u)
    w = f.weight(e, u)
    from conftest import expect_violation
    expect_violation(conn, "CNAI-HIST",
                     "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, "
                     "value_canonical, recorded_by, supersedes_id) VALUES (%s, 'anthro.weight_kg', 'present', 70, 'kg', 70, %s, %s)",
                     (other_encounter, u, w))
    expect_violation(conn, "CNAI-HIST",
                     "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, "
                     "value_canonical, recorded_by, supersedes_id) VALUES (%s, 'anthro.muac_cm', 'present', 30, 'cm', 30, %s, %s)",
                     (e, u, w))


def test_paediatric_elements_are_disabled_and_refused_even_on_the_adult_pathway(f, conn):
    from conftest import expect_violation
    u, _, e = f.case()
    expect_violation(conn, "CNAI-POP",
                     "INSERT INTO clinical.observation (encounter_id, element_code, status, value_numeric, unit_entered, "
                     "value_canonical, recorded_by) VALUES (%s, 'paed.muac_cm', 'present', 11.5, 'cm', 11.5, %s)", (e, u))
    # even a not_assessed placeholder for a disabled element is refused
    expect_violation(conn, "CNAI-POP",
                     "INSERT INTO clinical.observation (encounter_id, element_code, status, recorded_by) "
                     "VALUES (%s, 'paed.muac_cm', 'not_assessed', %s)", (e, u))


def test_catalogue_matches_the_json_source_of_truth(conn):
    import json
    from pathlib import Path
    src = json.loads((Path(__file__).resolve().parents[2] / "packages/schemas/data_elements.json").read_text())
    rows = conn.execute("SELECT element_code, enabled, value_type, canonical_unit FROM clinical.data_element_def").fetchall()
    db = {r[0]: r[1:] for r in rows}
    assert set(db) == {e["element_code"] for e in src["elements"]}
    for e in src["elements"]:
        assert db[e["element_code"]] == (e.get("enabled", True), e["value_type"], e.get("canonical_unit"))

"""The engine and the database must agree on scope, so neither layer is the only line of defence (FR-POP-02)."""
import itertools

import psycopg
import pytest

from cnai.engine import analyse
from cnai.testing import catalogue, make_case, rules

pytestmark = [pytest.mark.db, pytest.mark.safety]

PATHWAYS = ["adult_general", "paediatric_general", "paediatric_sam"]
AGES = [0, 6, 24, 59, 100, 215, 216, 300, 1000]


@pytest.mark.parametrize("pathway,age", list(itertools.product(PATHWAYS, AGES)))
def test_engine_refuses_exactly_when_the_database_refuses(f, conn, pathway, age):
    engine_refuses = analyse(make_case([], pathway=pathway, age_months=age), catalogue(), rules())["scope"]["status"] == "refused"
    u = f.user()
    p = f.patient()
    try:
        with conn.transaction():
            f.encounter(p, u, pathway=pathway, age=age)
        db_refuses = False
    except psycopg.errors.CheckViolation:
        db_refuses = True
    assert engine_refuses == db_refuses


def test_engine_catalogue_agrees_with_database_elements_for_the_adult_pathway(conn):
    rows = conn.execute("SELECT element_code, undated_ok, staleness_days, conflict_tolerance FROM clinical.data_element_def "
                        "WHERE enabled").fetchall()
    db = {r[0]: (r[1], r[2], None if r[3] is None else float(r[3])) for r in rows}
    cat = {c: (e.undated_ok, e.staleness_days, None if e.conflict_tolerance is None else float(e.conflict_tolerance))
           for c, e in catalogue().elements.items() if e.enabled}
    assert db == cat

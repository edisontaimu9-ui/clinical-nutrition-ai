"""Request sessions, sign-in resolution, identity vault and start-up governance checks (FR-GOV-03, NFR-SEC-01/02/05/09)."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from conftest import Factory, act, denied, grant, reset
from cnai.models import RuleSet
from cnai_security import audit, session, vault
from cnai_security.auth import AuthError, resolve_identity, verify_access_token
from cnai_security.crypto import CryptoError, LocalKeyProvider
from cnai_security.startup import StartupRefused, require_startup_ok, run_startup_checks

pytestmark = [pytest.mark.db, pytest.mark.safety]


# ------------------------------------------------------------------------------------------- sessions ---
def test_bind_sets_role_and_context_inside_the_transaction_only(conn):
    f = Factory(conn)
    u = f.user()
    grant(conn, u, "clinician")
    session.bind(conn, u, "cnai_clinician", "clinician", "10.0.0.1")
    assert conn.execute("SELECT current_user, current_setting('app.user_id'), current_setting('app.source_ip')").fetchone() == \
        ("cnai_clinician", str(u), "10.0.0.1")
    conn.rollback()                                       # end of request: nothing leaks to the next one on a pooled connection
    assert conn.execute("SELECT current_user <> 'cnai_clinician', coalesce(current_setting('app.user_id', true), '')").fetchone() == (True, "")


def test_bind_refuses_unknown_roles_and_autocommit(conn):
    with pytest.raises(ValueError):
        session.bind(conn, None, "postgres; DROP TABLE x")
    with pytest.raises(ValueError):
        session.bind(conn, None, "cnai_owner")
    conn.commit()
    conn.autocommit = True
    try:
        with pytest.raises(RuntimeError):
            session.bind(conn, None, "cnai_clinician")
    finally:
        conn.autocommit = False


# ------------------------------------------------------------------------------------------- sign-in ---
@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(key, sub, amr=("pwd", "otp")):
    now = int(time.time())
    return jwt.encode({"sub": sub, "iss": "https://idp.invalid/", "aud": "cnai-api", "iat": now, "exp": now + 300, "amr": list(amr)},
                      key, algorithm="RS256")


def test_a_verified_subject_resolves_to_the_database_user_and_current_roles(conn, rsa_key):
    f = Factory(conn)
    u = f.user()
    conn.execute("UPDATE iam.app_user SET external_subject = 'idp|abc' WHERE user_id = %s", (u,))
    grant(conn, u, "clinician")
    grant(conn, u, "supervisor")
    p = verify_access_token(make_token(rsa_key, "idp|abc"), key=rsa_key.public_key(), issuer="https://idp.invalid/", audience="cnai-api")
    act(conn, None, "cnai_auth_service")
    ident = resolve_identity(conn, p)
    assert ident.user_id == str(u) and ident.org_id == str(f.org) and ident.roles == ("clinician", "supervisor")


def test_roles_in_the_token_are_ignored_and_unknown_or_inactive_users_are_refused(conn, rsa_key):
    f = Factory(conn)
    u = f.user()
    conn.execute("UPDATE iam.app_user SET external_subject = 'idp|plain' WHERE user_id = %s", (u,))
    now = int(time.time())
    tok = jwt.encode({"sub": "idp|plain", "iss": "https://idp.invalid/", "aud": "cnai-api", "iat": now, "exp": now + 300,
                      "amr": ["mfa"], "roles": ["admin", "clinician"]}, rsa_key, algorithm="RS256")
    p = verify_access_token(tok, key=rsa_key.public_key(), issuer="https://idp.invalid/", audience="cnai-api")
    act(conn, None, "cnai_auth_service")
    assert resolve_identity(conn, p).roles == ()                    # the token's role claims grant nothing
    reset(conn)
    conn.execute("UPDATE iam.app_user SET is_active = false WHERE user_id = %s", (u,))
    act(conn, None, "cnai_auth_service")
    with pytest.raises(AuthError):
        resolve_identity(conn, p)
    other = verify_access_token(make_token(rsa_key, "idp|nobody"), key=rsa_key.public_key(), issuer="https://idp.invalid/", audience="cnai-api")
    with pytest.raises(AuthError):
        resolve_identity(conn, other)


def test_an_expired_role_grant_is_not_resolved(conn, rsa_key):
    f = Factory(conn)
    u = f.user()
    conn.execute("UPDATE iam.app_user SET external_subject = 'idp|exp' WHERE user_id = %s", (u,))
    grant(conn, u, "clinician")
    conn.execute("UPDATE iam.role_grant SET expires_at = now() - interval '1 second' WHERE user_id = %s", (u,))
    p = verify_access_token(make_token(rsa_key, "idp|exp"), key=rsa_key.public_key(), issuer="https://idp.invalid/", audience="cnai-api")
    act(conn, None, "cnai_auth_service")
    assert resolve_identity(conn, p).roles == ()


def test_the_auth_service_cannot_read_users_or_patients_directly(conn):
    act(conn, None, "cnai_auth_service")
    for t in ("iam.app_user", "iam.role_grant", "clinical.patient"):
        denied(conn, f"SELECT * FROM {t}")


# ------------------------------------------------------------------------------------------- identity vault ---
def vault_world(conn):
    f = Factory(conn)
    u1, u2 = f.user("c1"), f.user("c2")
    grant(conn, u1, "clinician")
    grant(conn, u2, "clinician")
    act(conn, u1)
    p1 = conn.execute("SELECT sec.register_patient('SYN-V1', 'female')").fetchone()[0]
    reset(conn)
    return f, u1, u2, p1


def test_identifiers_round_trip_encrypted_and_never_appear_in_plaintext(conn):
    f, u1, u2, p1 = vault_world(conn)
    kp = LocalKeyProvider.generate("k1")
    act(conn, u1, "cnai_identity_service")
    vault.store_identifiers(conn, p1, f.org, {"full_name": "Test Person", "phone": "SYN-PHONE-1"}, kp)
    assert vault.fetch_identifiers(conn, p1, kp) == {"full_name": "Test Person", "phone": "SYN-PHONE-1"}
    reset(conn)
    raw = bytes(conn.execute("SELECT encrypted_identifiers FROM identity.patient_identity").fetchone()[0])
    assert b"Test Person" not in raw and b"SYN-PHONE" not in raw
    audit_blob = str(conn.execute("SELECT details FROM audit.event WHERE action = 'identity.patient_identity.insert'").fetchone())
    assert "Test Person" not in audit_blob and "key_id" in audit_blob


def test_a_record_copied_to_another_patient_will_not_decrypt(conn):
    f, u1, u2, p1 = vault_world(conn)
    kp = LocalKeyProvider.generate("k1")
    act(conn, u2)
    p2 = conn.execute("SELECT sec.register_patient('SYN-V2', 'male')").fetchone()[0]
    act(conn, u1, "cnai_identity_service")
    vault.store_identifiers(conn, p1, f.org, {"full_name": "Test Person"}, kp)
    blob = bytes(conn.execute("SELECT encrypted_identifiers FROM identity.patient_identity WHERE patient_id = %s", (p1,)).fetchone()[0])
    reset(conn)
    conn.execute("INSERT INTO identity.patient_identity (patient_id, org_id, encrypted_identifiers, key_id) VALUES (%s, %s, %s, 'k1')", (p2, f.org, blob))
    act(conn, u2, "cnai_identity_service")
    with pytest.raises(CryptoError):
        vault.fetch_identifiers(conn, p2, kp)


def test_the_vault_respects_care_relationships_and_rejects_unknown_fields(conn):
    f, u1, u2, p1 = vault_world(conn)
    kp = LocalKeyProvider.generate("k1")
    act(conn, u2, "cnai_identity_service")
    with pytest.raises(Exception):
        with conn.transaction():
            vault.store_identifiers(conn, p1, f.org, {"full_name": "X"}, kp)            # u2 has no care relationship
    with pytest.raises(ValueError):
        vault.store_identifiers(conn, p1, f.org, {"favourite_colour": "blue"}, kp)
    act(conn, u2)
    assert conn.execute("SELECT count(*) FROM clinical.patient").fetchone()[0] == 0


def test_key_rotation_keeps_records_readable_under_the_new_key_only(conn):
    f, u1, u2, p1 = vault_world(conn)
    old, new = LocalKeyProvider.generate("k1"), LocalKeyProvider.generate("k2")
    act(conn, u1, "cnai_identity_service")
    vault.store_identifiers(conn, p1, f.org, {"full_name": "Test Person"}, old)
    vault.rotate_key(conn, p1, old, new)
    assert vault.fetch_identifiers(conn, p1, new) == {"full_name": "Test Person"}
    with pytest.raises(CryptoError):
        vault.fetch_identifiers(conn, p1, old)
    assert conn.execute("SELECT key_id FROM identity.patient_identity").fetchone()[0] == "k2"


# ------------------------------------------------------------------------------------------- start-up checks ---
class TLSConn:
    """Wrap a connection and report TLS in use, so the passing path can be exercised on a local socket."""
    def __init__(self, conn):
        self._c = conn
        self.pgconn = type("P", (), {"ssl_in_use": True})()

    def execute(self, *a, **k):
        return self._c.execute(*a, **k)


class ManagedKP(LocalKeyProvider):
    development_only = False


def reviewed_rules():
    import dataclasses
    return dataclasses.replace(RuleSet.load("adult_general"), review_status="independently_reviewed")


def names(rep):
    return {c.name for c in rep.failures}


def test_a_development_synthetic_instance_starts_with_a_visible_notice(conn):
    rep = require_startup_ok(conn)
    assert not rep.clinical and any(c.name == "development_notice" for c in rep.checks)


def test_clinical_mode_refuses_to_start_while_the_reviewer_is_vacant_and_rules_are_unreviewed(conn):
    conn.execute("UPDATE ops.instance_config SET mode = 'clinical'")
    with pytest.raises(StartupRefused) as e:
        require_startup_ok(conn, ruleset=RuleSet.load("adult_general"))
    failed = {c.name for c in e.value.failures}
    assert {"independent_reviewer_assigned", "rule_set_independently_reviewed", "tls_to_database",
            "key_provider_is_managed"} <= failed


def test_real_data_makes_the_instance_clinical_even_in_shadow_mode(conn):
    conn.execute("UPDATE ops.instance_config SET data_class = 'real', mode = 'shadow'")
    rep = run_startup_checks(conn, ruleset=RuleSet.load("adult_general"))
    assert rep.clinical and "independent_reviewer_assigned" in names(rep)


def test_a_fully_governed_clinical_instance_passes(conn):
    f = Factory(conn)
    u = f.user("reviewer")
    f.assign("independent_clinical_safety_reviewer", u)
    conn.execute("UPDATE ops.instance_config SET mode = 'clinical'")
    rep = require_startup_ok(TLSConn(conn), ruleset=reviewed_rules(), key_provider=ManagedKP.generate("k"), mfa_required=True)
    assert rep.clinical and rep.ok


@pytest.mark.parametrize("kwargs,failing", [
    ({"mfa_required": False}, "mfa_required"),
    ({"key_provider": None}, "key_provider_is_managed"),
    ({"key_provider": LocalKeyProvider.generate("dev")}, "key_provider_is_managed"),
    ({"ruleset": None}, "rule_set_independently_reviewed"),
    ({"hosted_adapters_present": True}, "no_hosted_llm_adapter"),
    ({"runtime_role": "postgres"}, "runtime_role_unprivileged"),
])
def test_each_clinical_requirement_is_enforced_on_its_own(conn, kwargs, failing):
    f = Factory(conn)
    f.assign("independent_clinical_safety_reviewer", f.user("reviewer"))
    conn.execute("UPDATE ops.instance_config SET data_class = 'real', mode = 'clinical'")
    base = {"ruleset": reviewed_rules(), "key_provider": ManagedKP.generate("k"), "mfa_required": True}
    base.update(kwargs)
    rep = run_startup_checks(TLSConn(conn), **base)
    assert names(rep) == {failing}


def test_an_unreviewed_rule_in_the_database_blocks_clinical_start(conn):
    f = Factory(conn)
    u = f.user("author")
    f.assign("independent_clinical_safety_reviewer", f.user("reviewer"))
    f.rule(u)
    conn.execute("UPDATE ops.instance_config SET mode = 'clinical'")
    rep = run_startup_checks(TLSConn(conn), ruleset=reviewed_rules(), key_provider=ManagedKP.generate("k"))
    assert names(rep) == {"database_rules_independently_reviewed"}


def test_a_broken_audit_chain_blocks_start_in_any_mode(conn):
    f = Factory(conn)
    f.user()
    conn.execute("ALTER TABLE audit.event DISABLE TRIGGER audit_event_no_update_delete")
    conn.execute("UPDATE audit.event SET outcome = 'failure' WHERE event_id = (SELECT min(event_id) FROM audit.event)")
    with pytest.raises(StartupRefused) as e:
        require_startup_ok(conn)
    assert {c.name for c in e.value.failures} == {"audit_chain_intact"}


def test_startup_cannot_be_satisfied_by_the_development_key_provider_or_local_tls_alone(conn):
    f = Factory(conn)
    f.assign("independent_clinical_safety_reviewer", f.user("reviewer"))
    conn.execute("UPDATE ops.instance_config SET mode = 'clinical'")
    rep = run_startup_checks(conn, ruleset=reviewed_rules(), key_provider=LocalKeyProvider.generate("dev"))
    assert {"tls_to_database", "key_provider_is_managed"} <= names(rep)

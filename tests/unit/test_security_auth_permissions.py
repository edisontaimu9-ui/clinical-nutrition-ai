"""Token verification (NFR-SEC-05) and the authorisation matrix (FR-ADM-01, NFR-SEC-03). No database needed."""
import re
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from cnai_security.auth import AuthError, has_mfa, verify_access_token
from cnai_security.permissions import DB_ROLE, MATRIX, PATIENT_PERMISSIONS, PermissionDenied, authorize, db_role_for, permissions_for, require

ISS, AUD = "https://idp.invalid/", "cnai-api"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, alg="RS256", **over):
    now = int(time.time())
    claims = {"sub": "user-1", "iss": ISS, "aud": AUD, "iat": now, "exp": now + 600, "amr": ["pwd", "otp"]}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg)


def verify(tok, key, **kw):
    return verify_access_token(tok, key=key.public_key(), issuer=ISS, audience=AUD, **kw)


def reason(tok, key, **kw):
    with pytest.raises(AuthError) as e:
        verify(tok, key, **kw)
    return e.value.reason


def test_a_valid_token_with_mfa_is_accepted(rsa_key):
    p = verify(token(rsa_key), rsa_key)
    assert p.subject == "user-1" and "otp" in p.amr


def test_es256_is_accepted_too():
    k = ec.generate_private_key(ec.SECP256R1())
    assert verify(token(k, "ES256"), k).subject == "user-1"


@pytest.mark.parametrize("amr,expected", [
    (["pwd"], False), ([], False), (None, False), (["pwd", "otp"], True), (["mfa"], True), (["hwk", "fpt"], True),
    (["otp"], False), (["pwd", "pwd"], False), (["MFA"], True),
])
def test_mfa_detection(amr, expected):
    assert has_mfa(amr) is expected


def test_a_password_only_token_is_refused(rsa_key):
    assert reason(token(rsa_key, amr=["pwd"]), rsa_key) == "mfa_required"
    assert reason(token(rsa_key, amr=None), rsa_key) == "mfa_required"


def test_mfa_can_only_be_waived_explicitly_by_the_caller(rsa_key):
    assert verify(token(rsa_key, amr=["pwd"]), rsa_key, require_mfa=False).subject == "user-1"


def test_expired_wrong_issuer_wrong_audience_and_missing_claims_are_refused(rsa_key):
    now = int(time.time())
    assert reason(token(rsa_key, iat=now - 4000, exp=now - 3000), rsa_key, leeway=0) == "token_expired"
    assert reason(token(rsa_key, iss="https://evil.invalid/"), rsa_key) == "issuer_mismatch"
    assert reason(token(rsa_key, aud="other"), rsa_key) == "audience_mismatch"
    assert reason(token(rsa_key, sub=None), rsa_key) == "claim_missing"
    assert reason(token(rsa_key, exp=None), rsa_key) == "claim_missing"


def test_long_lived_tokens_are_refused(rsa_key):
    now = int(time.time())
    assert reason(token(rsa_key, iat=now, exp=now + 86400), rsa_key) == "token_lifetime_too_long"


def test_a_token_signed_by_another_key_is_refused(rsa_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert reason(token(other), rsa_key) == "token_invalid"


def test_alg_none_and_hmac_confusion_are_refused(rsa_key):
    unsigned = jwt.encode({"sub": "x", "iss": ISS, "aud": AUD, "iat": int(time.time()), "exp": int(time.time()) + 60,
                           "amr": ["mfa"]}, key=None, algorithm="none")
    assert reason(unsigned, rsa_key) in {"token_invalid", "algorithm_not_allowed"}
    # HMAC key confusion: the verifier only ever accepts the asymmetric allow-list, so HS256 is refused outright
    from cnai_security.auth import ALGORITHMS
    assert set(ALGORITHMS) == {"RS256", "ES256"}
    for bad in (("HS256",), ("none",), ("RS256", "HS512")):
        with pytest.raises(AuthError) as e:
            verify_access_token("a.b.c", key=rsa_key.public_key(), issuer=ISS, audience=AUD, algorithms=bad)
        assert e.value.reason == "algorithm_not_allowed"



def test_empty_or_garbage_tokens_are_refused(rsa_key):
    assert reason("", rsa_key) == "token_missing"
    assert reason("not-a-jwt", rsa_key) == "token_invalid"


def test_auth_errors_never_echo_the_token(rsa_key):
    tok = token(rsa_key, amr=["pwd"])
    with pytest.raises(AuthError) as e:
        verify(tok, rsa_key)
    assert tok not in str(e.value)


# -------------------------------------------------------------------------------- authorisation matrix ---
def test_unknown_roles_and_empty_roles_grant_nothing():
    assert permissions_for(["wizard"]) == frozenset()
    assert permissions_for([]) == frozenset() and permissions_for(None) == frozenset()
    assert not authorize([], "patient.read")


def test_admin_auditor_and_engineer_have_no_patient_permissions():
    for role in ("admin", "auditor", "engineer"):
        assert not (MATRIX[role] & PATIENT_PERMISSIONS), role


def test_clinical_safety_lead_has_no_direct_patient_permissions():
    assert not (MATRIX["clinical_safety_lead"] & PATIENT_PERMISSIONS)
    assert "rule.review_independent" in MATRIX["clinical_safety_lead"]


def test_a_trainee_cannot_approve_but_a_supervisor_can_cosign():
    assert not authorize(["trainee"], "review.approve") and authorize(["trainee"], "review.submit")
    assert authorize(["supervisor"], "review.cosign") and not authorize(["clinician"], "review.cosign")


def test_only_admin_manages_users_roles_and_the_kill_switch():
    for perm in ("user.manage", "role.grant", "governance.appoint", "system.kill_switch"):
        holders = {r for r, p in MATRIX.items() if perm in p}
        assert holders == {"admin"}, (perm, holders)


def test_only_auditors_read_the_audit_log():
    assert {r for r, p in MATRIX.items() if "audit.read" in p} == {"auditor"}


def test_separation_of_duties_no_role_both_administers_and_reads_patients():
    for role, perms in MATRIX.items():
        assert not ({"user.manage", "role.grant", "governance.appoint"} & perms and PATIENT_PERMISSIONS & perms), role


def test_require_raises_with_the_permission_name():
    with pytest.raises(PermissionDenied):
        require(["admin"], "patient.read")
    require(["clinician"], "patient.read")


def test_database_role_mapping_is_least_privilege_and_total():
    assert db_role_for(["admin"]) == "cnai_admin" and db_role_for(["auditor"]) == "cnai_auditor"
    assert db_role_for(["clinician", "admin"]) == "cnai_clinician"
    with pytest.raises(PermissionDenied):
        db_role_for(["engineer"])
    with pytest.raises(PermissionDenied):
        db_role_for([])
    assert set(DB_ROLE) == set(MATRIX) - {"engineer"}


def test_the_matrix_matches_the_security_document_role_table():
    text = (Path(__file__).resolve().parents[2] / "SECURITY.md").read_text()
    section = text.split("## 3. Roles and access control")[1].split("Controls:")[0]
    documented = set(re.findall(r"^\| `(\w+)` \|", section, re.M))
    assert documented == set(MATRIX)

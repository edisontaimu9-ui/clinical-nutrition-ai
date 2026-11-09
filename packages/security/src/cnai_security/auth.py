"""Access-token verification (NFR-SEC-05, SECURITY.md section 4).

The identity provider (OIDC, self-hosted, D-001) issues signed JWTs. This module only VERIFIES tokens; it never issues
them and never fetches keys over the network: the caller supplies the verification key (for example from a pinned,
locally cached JWKS). Roles are never read from the token: they come from the database (sec.resolve_subject).

Checks: asymmetric signature from an allow-list of algorithms (so 'none' and HMAC key-confusion are impossible),
issuer, audience, expiry, issued-at, a short maximum lifetime, and a multi-factor authentication claim.
"""
from dataclasses import dataclass
from typing import Optional, Sequence

import jwt

ALGORITHMS = ("RS256", "ES256")
MFA_FACTORS = {"otp", "hwk", "swk", "fpt", "face", "retina", "sms", "tel", "pin"}   # RFC 8176 amr values (second factors)
DEFAULT_MAX_LIFETIME_SECONDS = 3600


class AuthError(Exception):
    """Token or session refused. `reason` is a short, stable code that is safe to log (never the token)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class Principal:
    subject: str
    amr: tuple
    issued_at: int
    expires_at: int


def has_mfa(amr: Sequence[str]) -> bool:
    values = {str(a).lower() for a in amr or ()}
    if "mfa" in values:
        return True
    return "pwd" in values and bool(values & MFA_FACTORS) or len(values & MFA_FACTORS) >= 2


def verify_access_token(token: str, *, key, issuer: str, audience: str, algorithms: Sequence[str] = ALGORITHMS,
                        require_mfa: bool = True, leeway: int = 30,
                        max_lifetime: int = DEFAULT_MAX_LIFETIME_SECONDS) -> Principal:
    if not token or not isinstance(token, str):
        raise AuthError("token_missing")
    bad = [a for a in algorithms if a not in ALGORITHMS]
    if bad:
        raise AuthError("algorithm_not_allowed", ",".join(bad))
    try:
        claims = jwt.decode(token, key=key, algorithms=list(algorithms), issuer=issuer, audience=audience, leeway=leeway,
                            options={"require": ["exp", "iat", "sub", "iss", "aud"]})
    except jwt.ExpiredSignatureError:
        raise AuthError("token_expired") from None
    except jwt.InvalidAudienceError:
        raise AuthError("audience_mismatch") from None
    except jwt.InvalidIssuerError:
        raise AuthError("issuer_mismatch") from None
    except jwt.MissingRequiredClaimError as exc:
        raise AuthError("claim_missing", str(exc.claim)) from None
    except jwt.InvalidAlgorithmError:
        raise AuthError("algorithm_not_allowed") from None
    except jwt.InvalidTokenError:
        raise AuthError("token_invalid") from None
    sub = claims["sub"]
    if not isinstance(sub, str) or not sub.strip():
        raise AuthError("subject_invalid")
    if claims["exp"] - claims["iat"] > max_lifetime:
        raise AuthError("token_lifetime_too_long", f"limit {max_lifetime}s")
    amr = tuple(claims.get("amr") or ())
    if require_mfa and not has_mfa(amr):
        raise AuthError("mfa_required")
    return Principal(subject=sub, amr=amr, issued_at=int(claims["iat"]), expires_at=int(claims["exp"]))


@dataclass(frozen=True)
class Identity:
    user_id: str
    org_id: str
    roles: tuple


def resolve_identity(conn, principal: Principal) -> Identity:
    """Map a verified subject to a database user and their current role grants. Run as cnai_auth_service."""
    row = conn.execute("SELECT user_id, org_id, roles FROM sec.resolve_subject(%s)", (principal.subject,)).fetchone()
    if row is None:
        raise AuthError("user_unknown_or_inactive")
    return Identity(user_id=str(row[0]), org_id=str(row[1]), roles=tuple(row[2]))

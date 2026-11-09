"""Authorisation matrix (FR-ADM-01, NFR-SEC-03): application-level role checks, default deny.

This is the first of two layers. The second is PostgreSQL row-level security, which still applies if this layer has a
bug. The matrix mirrors the role table in SECURITY.md section 3; tests compare the two.
"""
PATIENT_PERMISSIONS = frozenset({"patient.read", "patient.register", "patient.write", "analysis.run", "observation.write",
                                 "review.submit", "review.approve", "review.cosign"})

MATRIX = {
    "clinician": {"patient.read", "patient.register", "patient.write", "analysis.run", "observation.write",
                  "review.submit", "review.approve", "rule.author", "care.share"},
    # A trainee prepares work; approval needs a supervisor co-sign (Q-09 default).
    "trainee": {"patient.read", "patient.register", "patient.write", "analysis.run", "observation.write",
                "review.submit", "care.share"},
    "supervisor": {"patient.read", "patient.register", "patient.write", "analysis.run", "observation.write",
                   "review.submit", "review.approve", "review.cosign", "rule.author", "care.share", "breakglass.approve"},
    # Independent reviewer: reviews clinical logic; patient content only through approved, logged break-glass access.
    "clinical_safety_lead": {"rule.review_independent", "breakglass.approve", "breakglass.use", "audit.read_summary"},
    "admin": {"user.manage", "role.grant", "governance.appoint", "system.kill_switch", "config.change"},
    "auditor": {"audit.read", "audit.verify"},
    "engineer": set(),   # no production access; synthetic-only elsewhere through the owner role in development
}

# Database role used for each application role (the engineer has none: it never connects as an app role).
DB_ROLE = {"clinician": "cnai_clinician", "trainee": "cnai_clinician", "supervisor": "cnai_clinician",
           "clinical_safety_lead": "cnai_safety_lead", "admin": "cnai_admin", "auditor": "cnai_auditor"}


class PermissionDenied(PermissionError):
    pass


def permissions_for(roles) -> frozenset:
    out = set()
    for r in roles or ():
        out |= MATRIX.get(r, set())      # unknown roles grant nothing
    return frozenset(out)


def authorize(roles, permission: str) -> bool:
    return permission in permissions_for(roles)


def require(roles, permission: str) -> None:
    if not authorize(roles, permission):
        raise PermissionDenied(f"permission {permission!r} is not granted to roles {sorted(roles or ())}")


def db_role_for(roles) -> str:
    """The least-privileged database role covering the user's application roles; raises if there is none."""
    for r in ("clinician", "supervisor", "trainee", "clinical_safety_lead", "admin", "auditor"):
        if r in (roles or ()):
            return DB_ROLE[r]
    raise PermissionDenied("no application role maps to a database role")

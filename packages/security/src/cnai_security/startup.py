"""Start-up governance checks (FR-GOV-03, NFR-SEC-01/05/09, D-019, D-020).

An instance in clinical mode, or holding real data, must refuse to start unless the independent clinical safety reviewer
role is assigned and every active rule is independently reviewed, plus transport, key, authentication and egress
conditions. A development instance with synthetic data passes with a visible notice. Run this with a connection that can
read ops.*, clinical.rule and audit.event (the owner or a start-up role), before serving any request.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from . import audit


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


class StartupRefused(RuntimeError):
    def __init__(self, failures: List[Check]):
        self.failures = failures
        super().__init__("start-up refused: " + "; ".join(f"{c.name}: {c.detail}" for c in failures))


@dataclass
class StartupReport:
    data_class: str
    mode: str
    clinical: bool
    checks: List[Check] = field(default_factory=list)

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def ok(self) -> bool:
        return not self.failures


def run_startup_checks(conn, *, ruleset=None, key_provider=None, mfa_required: bool = True,
                       hosted_adapters_present: bool = False, runtime_role: Optional[str] = None) -> StartupReport:
    data_class, mode = conn.execute("SELECT data_class, mode FROM ops.instance_config").fetchone()
    clinical = mode == "clinical" or data_class == "real"
    rep = StartupReport(data_class, mode, clinical)
    add = lambda name, ok, detail: rep.checks.append(Check(name, bool(ok), detail))  # noqa: E731

    # Always: data class and patient flags agree, and the audit chain is intact.
    bad = conn.execute("SELECT count(*) FROM clinical.patient WHERE is_synthetic <> (%s = 'synthetic')", (data_class,)).fetchone()[0]
    add("data_class_consistent", bad == 0, "all patients match the instance data class" if bad == 0 else f"{bad} patient(s) do not match data class {data_class}")
    chain = audit.verify_chain(conn)
    add("audit_chain_intact", chain.ok, f"{chain.checked} events verified" if chain.ok else f"event {chain.bad_event_id}: {chain.reason}")

    if not clinical:
        add("development_notice", True, "development instance with synthetic data: clinical-mode requirements are not enforced and no clinical use is permitted")
        return rep

    # Clinical mode or real data.
    reviewer = conn.execute("SELECT status FROM ops.governance_role WHERE role_key = 'independent_clinical_safety_reviewer'").fetchone()
    add("independent_reviewer_assigned", reviewer is not None and reviewer[0] == "assigned",
        "role assigned" if reviewer and reviewer[0] == "assigned" else "the independent clinical safety reviewer role is vacant")
    unreviewed = conn.execute("SELECT count(*) FROM clinical.rule WHERE review_status <> 'independently_reviewed'").fetchone()[0]
    add("database_rules_independently_reviewed", unreviewed == 0,
        "all rules in the database are independently reviewed" if unreviewed == 0 else f"{unreviewed} rule(s) are not independently reviewed")
    if ruleset is None:
        add("rule_set_independently_reviewed", False, "no rule set was provided to check")
    else:
        add("rule_set_independently_reviewed", ruleset.review_status == "independently_reviewed",
            f"rule set {ruleset.rule_set} v{ruleset.version} is {ruleset.review_status}")
    kb = conn.execute("SELECT to_regclass('kb.source')").fetchone()[0]
    if kb is None:
        add("kb_sources_independently_reviewed", True, "no knowledge base exists yet (Phase 4)")
    else:
        n = conn.execute("SELECT count(*) FROM kb.source WHERE active AND review_status <> 'independently_reviewed'").fetchone()[0]
        add("kb_sources_independently_reviewed", n == 0, f"{n} active knowledge-base source(s) are not independently reviewed" if n else "all active sources are independently reviewed")

    add("tls_to_database", bool(conn.pgconn.ssl_in_use), "database connection is encrypted" if conn.pgconn.ssl_in_use else "database connection is not using TLS")
    managed = key_provider is not None and not getattr(key_provider, "development_only", True)   # providers must opt in
    add("key_provider_is_managed", managed,
        "a managed key provider is configured" if managed else "no managed key provider (a development key provider is not allowed here)")
    add("mfa_required", mfa_required, "multi-factor authentication is required" if mfa_required else "multi-factor authentication is disabled")
    add("no_hosted_llm_adapter", not (data_class == "real" and hosted_adapters_present),
        "no hosted LLM adapter present" if not hosted_adapters_present else "a hosted LLM adapter is present in a real-data instance")
    if runtime_role is not None:
        priv = conn.execute("SELECT rolsuper OR rolbypassrls OR rolcreaterole FROM pg_roles WHERE rolname = %s", (runtime_role,)).fetchone()
        add("runtime_role_unprivileged", priv is not None and not priv[0],
            f"{runtime_role} is not a superuser" if priv is not None and not priv[0] else f"{runtime_role} is privileged or unknown")
    return rep


def require_startup_ok(conn, **kw) -> StartupReport:
    rep = run_startup_checks(conn, **kw)
    if not rep.ok:
        raise StartupRefused(rep.failures)
    return rep

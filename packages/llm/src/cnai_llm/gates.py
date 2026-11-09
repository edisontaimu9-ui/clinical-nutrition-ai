"""Hosted-backend gates (NFR-SEC-09, SECURITY.md 7.3). A hosted call is allowed only if EVERY check passes; the first failure
refuses the call before any outbound request. Gates fail closed: unknown values refuse."""
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from . import idscan

HOSTED_BUILD_PROFILES = frozenset({"synthetic-dev"})       # every other profile (real-data, clinical, unknown) excludes hosted


@dataclass(frozen=True)
class InstanceInfo:
    data_class: str
    mode: str


@dataclass
class GateDecision:
    allowed: bool
    code: str = "ok"
    detail: str = ""
    checks: List[str] = field(default_factory=list)


class HostedGate:
    def __init__(self, instance: Callable[[], InstanceInfo], case_is_synthetic: Callable[[str], Optional[bool]],
                 build_profile: str = "synthetic-dev", deny_names: Iterable[str] = ()):
        self._instance, self._case, self.profile, self.deny_names = instance, case_is_synthetic, build_profile, tuple(deny_names)

    def check(self, cfg: Dict, case_id: str, prompt_text: str) -> GateDecision:
        passed: List[str] = []

        def refuse(code, detail=""):
            return GateDecision(False, code, detail, passed)

        if self.profile not in HOSTED_BUILD_PROFILES:
            return refuse("build_profile_excludes_hosted", f"profile {self.profile!r}")
        passed.append("build_profile")
        info = self._instance()                                   # read on EVERY request, not cached
        if info.data_class != "synthetic":
            return refuse("instance_not_synthetic", f"data class {info.data_class!r}")
        passed.append("instance_data_class")
        if info.mode == "clinical":
            return refuse("instance_mode_clinical")
        passed.append("instance_mode")
        syn = self._case(case_id)
        if syn is None:
            return refuse("case_unknown")
        if syn is not True:
            return refuse("case_not_synthetic")
        passed.append("case_synthetic")
        if not cfg.get("enabled", False):
            return refuse("backend_disabled")
        passed.append("backend_enabled")
        if not cfg.get("provider_terms_ref"):
            return refuse("terms_not_recorded", "provider terms must be recorded in the decision log before first use")
        passed.append("provider_terms")
        ver = str(cfg.get("model_version") or "")
        if not ver or ver.lower() in {"latest", "default", "stable", "*"}:
            return refuse("version_not_pinned")
        passed.append("version_pinned")
        host = urlparse(cfg.get("endpoint", "")).hostname
        if not host or host not in set(cfg.get("endpoint_allowlist", [])):
            return refuse("endpoint_not_allowed")
        passed.append("endpoint_allowlist")
        found = idscan.scan(prompt_text, self.deny_names)
        if found:
            return refuse("identifier_pattern", ", ".join(f"{k}x{n}" for k, n in sorted(found.items())))
        passed.append("identifier_scan")
        return GateDecision(True, "ok", "", passed)


def instance_from_db(conn) -> Callable[[], InstanceInfo]:
    def read() -> InstanceInfo:
        row = conn.execute("SELECT data_class, mode FROM ops.instance_config").fetchone()
        return InstanceInfo(row[0], row[1]) if row else InstanceInfo("unknown", "unknown")
    return read


def case_source_from_db(conn) -> Callable[[str], Optional[bool]]:
    """case_id is an encounter id. True only if the patient is flagged synthetic; None if it cannot be found."""
    def read(case_id: str) -> Optional[bool]:
        try:
            row = conn.execute("SELECT p.is_synthetic FROM clinical.encounter e JOIN clinical.patient p ON p.patient_id = e.patient_id "
                               "WHERE e.encounter_id = %s", (case_id,)).fetchone()
        except Exception:
            return None
        return None if row is None else bool(row[0])
    return read


def case_source_from_cases(cases: Dict[str, object]) -> Callable[[str], Optional[bool]]:
    """For file-based synthetic cases: the case object's own flag, looked up by id (not supplied by the caller)."""
    def read(case_id: str) -> Optional[bool]:
        c = cases.get(case_id)
        return None if c is None else bool(getattr(c, "synthetic", False))
    return read

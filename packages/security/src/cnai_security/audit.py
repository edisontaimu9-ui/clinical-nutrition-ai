"""Audit log helpers: write application events, verify the hash chain independently of the database, and sign anchors.

The chain formula is implemented here a second time, on purpose: the database computes hashes when events are written
(audit.compute_hash) and this module recomputes them from the stored fields, so a mistake or tampering on either side
shows up as a disagreement. A chain alone cannot stop someone who controls the whole database from rewriting history
and recomputing every hash, so the head is also exported as a signed anchor and kept outside the database (FR-AUD-03).
"""
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

SEP = "\x1f"
_SCALARS = (str, int, bool, type(None))
_FORBIDDEN_KEYS = {"note", "notes", "text", "free_text", "comment", "message", "value", "values", "diagnosis", "name", "address"}
MAX_DETAIL_STRING = 120


class AuditDetailError(ValueError):
    """Audit details must be small, flat and free of clinical content."""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    checked: int
    bad_event_id: Optional[int] = None
    reason: Optional[str] = None
    head_event_id: Optional[int] = None
    head_hash: Optional[str] = None


def ts_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def event_hash(event_id, occurred_at, actor_id, actor_role, action, resource_type, resource_id, patient_id, outcome,
               source_ip, request_id, details_text, prev_hash_hex) -> bytes:
    fields = [str(event_id), ts_text(occurred_at), str(actor_id) if actor_id else "", actor_role or "", action,
              resource_type, resource_id or "", str(patient_id) if patient_id else "", outcome, source_ip or "",
              request_id or "", details_text, prev_hash_hex or ""]
    return hashlib.sha256(SEP.join(fields).encode("utf-8")).digest()


def check_details(details: Optional[dict]) -> dict:
    """Allow only flat, short, non-clinical details (SECURITY.md section 10: no clinical free text in the audit log)."""
    details = details or {}
    for k, v in details.items():
        if not isinstance(v, _SCALARS):
            raise AuditDetailError(f"audit detail {k!r} must be a scalar")
        if k.lower() in _FORBIDDEN_KEYS:
            raise AuditDetailError(f"audit detail key {k!r} is not allowed (could carry clinical content)")
        if isinstance(v, str) and len(v) > MAX_DETAIL_STRING:
            raise AuditDetailError(f"audit detail {k!r} is longer than {MAX_DETAIL_STRING} characters")
    return details


def record_event(conn, action: str, resource_type: str, resource_id: Optional[str] = None, patient_id=None,
                 outcome: str = "success", request_id: Optional[str] = None, details: Optional[dict] = None) -> int:
    """Write an application event (reads, exports, authentication, kill switch, ...). The actor is the request's user
    and cannot be chosen by the caller; the action must be in the database vocabulary."""
    d = check_details(details)
    return conn.execute("SELECT sec.record_event(%s, %s, %s, %s, %s, %s, %s::jsonb)",
                        (action, resource_type, resource_id, patient_id, outcome, request_id, json.dumps(d))).fetchone()[0]


def verify_chain(conn, up_to_event_id: Optional[int] = None) -> VerifyResult:
    """Recompute every hash and every link. Must run as a role that can read audit.event (auditor or owner)."""
    cur = conn.execute(
        "SELECT event_id, occurred_at, actor_id, actor_role, action, resource_type, resource_id, patient_id, outcome, "
        "source_ip, request_id, details::text, encode(prev_hash, 'hex'), encode(hash, 'hex') FROM audit.event ORDER BY event_id")
    prev, n, head_id, head = None, 0, None, None
    for row in cur:
        (eid, ts, actor, role, action, rtype, rid, patient, outcome, ip, req, details, prev_hex, hash_hex) = row
        if up_to_event_id is not None and eid > up_to_event_id:
            break
        if (prev_hex or None) != prev:
            why = "first event has a previous hash" if n == 0 else "link to previous event is broken (event removed, inserted or reordered)"
            return VerifyResult(False, n, eid, why, head_id, head)
        if event_hash(eid, ts, actor, role, action, rtype, rid, patient, outcome, ip, req, details, prev_hex).hex() != hash_hex:
            return VerifyResult(False, n, eid, "event content does not match its hash", head_id, head)
        prev, n, head_id, head = hash_hex, n + 1, eid, hash_hex
    return VerifyResult(True, n, None, None, head_id, head)


def _mac(anchor: dict, key: bytes) -> str:
    body = {k: anchor[k] for k in ("version", "event_id", "head_hash", "count", "created_at")}
    return hmac.new(key, json.dumps(body, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def make_anchor(conn, key: bytes) -> dict:
    """Verify the chain, then export its head signed with a key that is NOT stored in the database. Keep the result
    somewhere the database administrator cannot write (separate store, printed, or a signed commit)."""
    res = verify_chain(conn)
    if not res.ok:
        raise RuntimeError(f"refusing to anchor a broken chain at event {res.bad_event_id}: {res.reason}")
    if res.head_event_id is None:
        raise RuntimeError("the audit log is empty; nothing to anchor")
    a = {"version": 1, "event_id": res.head_event_id, "head_hash": res.head_hash, "count": res.checked,
         "created_at": datetime.now(timezone.utc).isoformat()}
    a["mac"] = _mac(a, key)
    return a


def check_anchor(conn, anchor: dict, key: bytes) -> VerifyResult:
    """The chain must verify AND still contain the anchored head unchanged (detects truncation and full rewrites)."""
    if not hmac.compare_digest(anchor.get("mac", ""), _mac(anchor, key)):
        return VerifyResult(False, 0, None, "anchor signature is invalid")
    res = verify_chain(conn, up_to_event_id=anchor["event_id"])
    if not res.ok:
        return res
    if res.head_event_id != anchor["event_id"] or res.head_hash != anchor["head_hash"] or res.checked != anchor["count"]:
        return VerifyResult(False, res.checked, anchor["event_id"],
                            "the log no longer matches the signed anchor (events removed, replaced or history rewritten)")
    return res

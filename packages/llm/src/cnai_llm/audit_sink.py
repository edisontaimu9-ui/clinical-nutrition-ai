"""Where model-run records go (FR-AUD-02). Hashes and identifiers only: never prompt or output text."""
from typing import Any, Dict, List, Protocol


class AuditSink(Protocol):
    def record(self, action: str, run_id: str, outcome: str, details: Dict[str, Any]) -> None: ...


class MemoryAuditSink:
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def record(self, action, run_id, outcome, details):
        self.events.append({"action": action, "run_id": run_id, "outcome": outcome, "details": dict(details)})


class DatabaseAuditSink:
    """Writes to the hash-chained audit log through sec.record_event (actor comes from the session)."""

    def __init__(self, conn):
        self.conn = conn

    def record(self, action, run_id, outcome, details):
        from cnai_security import audit
        db_outcome = {"model.run": "success", "model.refused": "denied"}.get(action, "failure")
        audit.record_event(self.conn, action, "llm.gateway", resource_id=run_id, outcome=db_outcome,
                           details={**details, "result": outcome})

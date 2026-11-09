"""Shared types and errors for the gateway. Nothing here knows about any provider."""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


class GatewayError(Exception):
    """Base class. `code` is a short stable string that is safe to log; `detail` never contains prompt, output or keys."""
    code = "gateway_error"

    def __init__(self, detail: str = ""):
        super().__init__(f"{self.code}: {detail}" if detail else self.code)
        self.detail = detail


class BackendUnavailable(GatewayError):
    code = "backend_unavailable"


class BackendTimeout(GatewayError):
    code = "backend_timeout"


class RateLimited(GatewayError):
    code = "rate_limited"


class BackendProtocolError(GatewayError):
    code = "backend_protocol_error"


@dataclass(frozen=True)
class BackendInfo:
    backend_id: str
    mode: str                    # mock | hosted | local
    provider: str
    model_id: str
    model_version: str           # pinned version or snapshot date; never "latest"
    digest: Optional[str] = None  # model digest where the runtime provides one (local runtimes)


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str = ""
    latency_s: float = 0.0


class Backend(Protocol):
    info: BackendInfo

    def complete(self, system: str, user: str, *, params: Dict[str, Any]) -> Completion: ...


@dataclass(frozen=True)
class GenerationRequest:
    """What a caller may ask for. The caller cannot assert that a case is synthetic: the gate looks that up itself."""
    task_id: str
    case_id: str
    inputs: Dict[str, Any]
    prompt_id: str


@dataclass
class GatewayResult:
    status: str                              # "ok" or "unable_to_draft" (fail safe, NFR-SAF-04)
    output: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None             # stable code when not ok
    detail: str = ""
    run_id: str = ""
    backend: Optional[BackendInfo] = None
    prompt_id: str = ""
    prompt_version: str = ""
    input_sha256: str = ""
    output_sha256: str = ""
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    violations: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

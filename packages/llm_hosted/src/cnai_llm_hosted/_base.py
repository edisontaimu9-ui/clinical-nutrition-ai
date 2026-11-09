import time
from typing import Any, Dict, Optional, Tuple

from cnai_llm.types import BackendInfo, BackendProtocolError, Completion

from .transport import HttpsTransport


class HostedBase:
    path = ""

    def __init__(self, *, backend_id, provider, model_id, model_version, endpoint, secret, transport=None, params=None,
                 endpoint_allowlist: Tuple[str, ...] = ()):
        self.info = BackendInfo(backend_id, "hosted", provider, model_id, str(model_version), None)
        self.endpoint, self.secret, self.p = endpoint.rstrip("/"), secret, dict(params or {})
        self.transport = transport or HttpsTransport(endpoint_allowlist)

    def _headers(self) -> Dict[str, str]:
        raise NotImplementedError

    def _body(self, system: str, user: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def _extract(self, data: Dict[str, Any]) -> Tuple[str, int, int, str]:
        raise NotImplementedError

    def complete(self, system: str, user: str, *, params: Dict[str, Any]) -> Completion:
        if self.secret is None:
            from cnai_llm.types import BackendUnavailable
            raise BackendUnavailable("no API key configured")
        merged = {**self.p, **params}
        t0 = time.monotonic()
        data = self.transport.post_json(self.endpoint + self.path, self._headers(), self._body(system, user, merged),
                                        float(merged.get("timeout_s", 60)))
        try:
            text, tin, tout, rid = self._extract(data)
        except (KeyError, IndexError, TypeError):
            raise BackendProtocolError("unexpected response shape") from None
        if not isinstance(text, str):
            raise BackendProtocolError("unexpected response shape")
        return Completion(text, int(tin or 0), int(tout or 0), str(rid or ""), time.monotonic() - t0)

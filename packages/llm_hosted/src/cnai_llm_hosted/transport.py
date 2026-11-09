"""The ONLY module in the project allowed to open a network connection (checked by tests/safety/test_egress_static.py).
https only, host allow-list, no redirects, response size cap, timeouts. Never logs or returns request headers."""
import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from cnai_llm.types import BackendProtocolError, BackendTimeout, BackendUnavailable, RateLimited

MAX_RESPONSE_BYTES = 2_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class HttpsTransport:
    """post_json(url, headers, body, timeout) -> (status, parsed_json). Errors map to the gateway's error types."""

    def __init__(self, allowlist: Tuple[str, ...]):
        self.allowlist = tuple(allowlist)

    def post_json(self, url: str, headers: Dict[str, str], body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in self.allowlist:
            raise BackendUnavailable("endpoint is not permitted")
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=timeout) as r:
                raw = r.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited("provider rate limit") from None
            if e.code >= 500 or e.code in (301, 302, 303, 307, 308):
                raise BackendUnavailable(f"provider status {e.code}") from None
            raise BackendProtocolError(f"provider status {e.code}") from None
        except (socket.timeout, TimeoutError):
            raise BackendTimeout("provider timed out") from None
        except (urllib.error.URLError, OSError):
            raise BackendUnavailable("provider unreachable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise BackendProtocolError("response too large")
        try:
            return json.loads(raw)
        except ValueError:
            raise BackendProtocolError("response was not JSON") from None

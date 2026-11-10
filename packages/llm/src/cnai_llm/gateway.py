"""The provider-neutral gateway (ARCHITECTURE 10.2). Every model call in the system goes through Gateway.generate.

Flow: resolve backend -> render versioned prompt -> (hosted only) gates + budget -> call -> parse -> schema-validate
(one repair retry) -> deterministic verifier -> audit. Any failure returns status "unable_to_draft" (NFR-SAF-04);
nothing partial is ever returned as a result. Audit records carry hashes and ids only (FR-AUD-02).
"""
import hashlib
import json
import os
import uuid
from typing import Any, Callable, Dict, Mapping, Optional

import jsonschema

from . import summary_task
from .audit_sink import AuditSink, MemoryAuditSink
from .gates import HostedGate
from .keys import KeyError_
from .prompts import PromptError, load_prompt
from .registry import Registry, RegistryError
from .spend import Budget, BudgetExceeded, SpendTracker
from .types import (Backend, BackendProtocolError, BackendTimeout, BackendUnavailable, GatewayError, GatewayResult,
                    GenerationRequest, RateLimited)

REPAIR_NOTE = ("Your previous reply was not valid. Reply again with ONLY a single JSON object that matches the schema. "
               "Use only facts from the data block. Do not add anything else.")

TASKS: Dict[str, Dict[str, Any]] = {
    summary_task.TASK_ID: {"schema": summary_task.load_schema, "verify": summary_task.verify},
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


class Gateway:
    def __init__(self, registry: Registry, *, gate: Optional[HostedGate] = None, sink: Optional[AuditSink] = None,
                 profile: str = "synthetic-dev", environ: Mapping[str, str] = os.environ, transport: Optional[object] = None,
                 clock: Callable[[], float] = None, canaries: tuple = ("CANARY-7731",),
                 sleep: Callable[[float], None] = None, progress: Optional[Callable[[str], None]] = None, capture_raw: bool = False):
        import time
        self.registry, self.gate, self.sink = registry, gate, sink or MemoryAuditSink()
        self.profile, self.environ, self.transport = profile, environ, transport
        self.clock, self.canaries = clock or time.time, canaries
        self.sleep, self.progress, self.capture_raw = sleep or time.sleep, progress, capture_raw
        self._preload: Dict[str, tuple] = {}                 # backend id -> (day, calls, tokens) restored from a checkpoint
        self._cooldown: Dict[str, float] = {}               # backend id -> time before which no request is sent (after a final HTTP 429)
        self._backends: Dict[str, Backend] = {}
        self._trackers: Dict[str, SpendTracker] = {}

    # -- helpers -----------------------------------------------------------------------------------------------
    def _backend(self, bid: str) -> Backend:
        if bid not in self._backends:
            self._backends[bid] = self.registry.build(bid, profile=self.profile, environ=self.environ, transport=self.transport)
        return self._backends[bid]

    def _tracker(self, bid: str, cfg: Dict[str, Any]) -> SpendTracker:
        if bid not in self._trackers:
            b = cfg.get("budget")
            if not b:
                raise BudgetExceeded("no budget configured for this hosted backend")
            self._trackers[bid] = SpendTracker(Budget(**b), clock=self.clock)
            if bid in self._preload:
                self._trackers[bid].preload(*self._preload[bid])
        return self._trackers[bid]

    def usage(self, bid: str):
        t = self._trackers.get(bid)
        return t.usage() if t else None

    def _audit(self, action, run_id, outcome, **details):
        try:
            self.sink.record(action, run_id, outcome, details)
        except Exception as e:       # report only the exception TYPE: the cause may contain content
            raise GatewayError(f"audit write failed ({type(e).__name__})") from None

    def _reserve(self, tracker, est_in, max_out, cfg):
        """Reserve budget. Per-minute limits may be waited out (controlled pacing) when the backend config allows it;
        daily limits never are."""
        retry = cfg.get("retry", {})
        waits = 0
        while True:
            try:
                tracker.reserve(est_in, max_out)
                return
            except BudgetExceeded as e:
                if not (retry.get("pace_on_rate_limit") and e.retry_after_s > 0 and waits < 8
                        and e.retry_after_s <= float(retry.get("max_wait_s", 90))):
                    raise
                waits += 1
                if self.progress:
                    self.progress(f"pacing: waiting {e.retry_after_s:.0f}s for the per-minute limit")
                self.sleep(e.retry_after_s)

    def _complete(self, backend, bid, system, text, params, tracker, cfg):
        """Call the backend. HTTP 429 gets a small number of controlled retries (Retry-After honoured, exponential
        backoff, capped); when they are exhausted the backend goes into cooldown and no further requests are sent."""
        retry = cfg.get("retry", {})
        max_retries = int(retry.get("max_429_retries", 2)) if cfg.get("mode") == "hosted" else 0
        n = t = 0
        while True:
            try:
                return backend.complete(system, text, params=params)
            except (BackendUnavailable, BackendTimeout) as e:
                # a transient network failure (dropped connection, timeout) gets a small, controlled retry; then it fails safe
                max_t = int(retry.get("max_transient_retries", 2)) if cfg.get("mode") == "hosted" else 0
                if t >= max_t:
                    raise
                delay = min(float(retry.get("backoff_s", 5)) * 2 ** t, float(retry.get("max_wait_s", 90)))
                t += 1
                if self.progress:
                    self.progress(f"{e.code}: retry {t}/{max_t} after {delay:.0f}s")
                self.sleep(delay)
                if tracker:
                    self._reserve(tracker, max(1, len(system + text) // 3), int(params.get("max_output_tokens", 1500)), cfg)
            except RateLimited as e:
                if n >= max_retries:
                    self._cooldown[bid] = self.clock() + float(retry.get("cooldown_s", 300))
                    raise
                delay = min(max(e.retry_after, float(retry.get("backoff_s", 5)) * 2 ** n), float(retry.get("max_wait_s", 90)))
                n += 1
                if self.progress:
                    self.progress(f"HTTP 429: retry {n}/{max_retries} after {delay:.0f}s")
                self.sleep(delay)
                if tracker:
                    self._reserve(tracker, max(1, len(system + text) // 3), int(params.get("max_output_tokens", 1500)), cfg)

    # -- main entry --------------------------------------------------------------------------------------------
    def generate(self, req: GenerationRequest, backend_id: Optional[str] = None) -> GatewayResult:
        run_id = uuid.uuid4().hex
        res = GatewayResult(status="unable_to_draft", run_id=run_id, prompt_id=req.prompt_id)
        cfg: Dict[str, Any] = {}
        try:
            task = TASKS.get(req.task_id)
            if task is None:
                return self._fail(res, "unknown_task", refused=True)
            bid = backend_id or self.registry.active_id(self.environ)
            cfg = self.registry.config(bid)
            prompt = load_prompt(req.prompt_id)
            res.prompt_version = prompt.version
            schema = task["schema"]()
            system, user = prompt.render(req.inputs, schema)
            res.input_sha256 = sha(system + "\n" + user)

            if cfg["mode"] == "hosted":
                if self.gate is None:
                    return self._fail(res, "gate_missing", refused=True, cfg=cfg)
                dec = self.gate.check(cfg, req.case_id, _canon(req.inputs))   # scan the data; templates are project-controlled
                if not dec.allowed:
                    return self._fail(res, dec.code, detail=dec.detail, refused=True, cfg=cfg)
                backend = self._backend(bid)
                params = dict(cfg.get("params", {}))
                tracker = self._tracker(bid, cfg)
                if self._cooldown.get(bid, 0) > self.clock():
                    return self._fail(res, "rate_limited_cooldown", refused=True, cfg=cfg)
                est_in, max_out = max(1, len(system + user) // 3), int(params.get("max_output_tokens", 1500))
                self._reserve(tracker, est_in, max_out, cfg)
            else:
                backend = self._backend(bid)
                params, tracker = dict(cfg.get("params", {})), None
            res.backend = backend.info

            text = user
            for attempt in (1, 2):
                res.attempts = attempt
                comp = self._complete(backend, bid, system, text, params, tracker, cfg)
                res.input_tokens += comp.input_tokens
                res.output_tokens += comp.output_tokens
                res.latency_s += comp.latency_s
                if tracker:
                    tracker.record(comp.input_tokens, comp.output_tokens)
                res.output_sha256 = sha(comp.text or "")
                if self.capture_raw:
                    res.raw_text = comp.text
                parsed, problem = self._parse(comp.text, schema)
                if parsed is not None:
                    break
                if attempt == 2:
                    return self._fail(res, problem, cfg=cfg)
                text = user + "\n\n" + REPAIR_NOTE
            viol = task["verify"](parsed, req.inputs, self.canaries)
            if viol:
                res.violations = viol
                return self._fail(res, "verification_failed", detail=",".join(sorted({v["code"] for v in viol})), cfg=cfg)
            res.status, res.output = "ok", parsed
            self._audit("model.run", run_id, "success", **self._facts(res, cfg))
            return res
        except GatewayError as e:
            return self._fail(res, e.code, detail=e.detail, refused=isinstance(e, BudgetExceeded), cfg=cfg)
        except KeyError_:
            return self._fail(res, "key_unavailable", refused=True, cfg=cfg)
        except (RegistryError, PromptError) as e:
            return self._fail(res, "configuration_error", detail=type(e).__name__, refused=True, cfg=cfg)

    def _parse(self, text: str, schema):
        if not text or not text.strip():
            return None, "empty_output"
        try:
            obj = json.loads(text)
        except (ValueError, RecursionError):
            return None, "invalid_json"
        if not isinstance(obj, dict):
            return None, "not_an_object"
        try:
            jsonschema.validate(obj, schema)
        except jsonschema.ValidationError:
            return None, "schema_invalid"
        return obj, ""

    def _facts(self, res: GatewayResult, cfg) -> Dict[str, Any]:
        b = res.backend
        return {"backend_id": b.backend_id if b else cfg.get("backend_id"), "mode": cfg.get("mode"),
                "model_id": b.model_id if b else None, "model_version": b.model_version if b else None,
                "prompt_id": res.prompt_id, "prompt_version": res.prompt_version, "input_sha256": res.input_sha256,
                "output_sha256": res.output_sha256, "attempts": res.attempts, "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens}

    def _fail(self, res: GatewayResult, code: str, detail: str = "", refused: bool = False, cfg=None) -> GatewayResult:
        res.status, res.output, res.reason, res.detail = "unable_to_draft", None, code, detail
        self._audit("model.refused" if refused else "model.failed", res.run_id, code, reason=code, detail=detail,
                    **self._facts(res, cfg or {}))
        return res

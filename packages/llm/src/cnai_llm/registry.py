"""Model and provider version registry (FR-ADM-02, NFR-ARC-03). Backends are chosen by configuration only."""
import importlib
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from .gates import HOSTED_BUILD_PROFILES
from .keys import load_secret
from .types import Backend, BackendUnavailable

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "backends.yaml"
ALLOWED_ADAPTER_PREFIXES = ("cnai_llm.", "cnai_llm_hosted.")
MODES = ("mock", "hosted", "local")


class RegistryError(ValueError):
    pass


class Registry:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.version = str(raw.get("registry_version", ""))
        self.backends: Dict[str, Dict[str, Any]] = raw.get("backends", {})
        for bid, cfg in self.backends.items():
            if cfg.get("mode") not in MODES:
                raise RegistryError(f"{bid}: mode must be one of {MODES}")
            if not str(cfg.get("adapter", "")).startswith(ALLOWED_ADAPTER_PREFIXES):
                raise RegistryError(f"{bid}: adapter must live in an allowed adapter package")
            if cfg["mode"] == "hosted" and not str(cfg["adapter"]).startswith("cnai_llm_hosted."):
                raise RegistryError(f"{bid}: hosted adapters must be in cnai_llm_hosted (excluded from real-data builds)")
            if cfg["mode"] == "mock" and str(cfg["adapter"]).startswith("cnai_llm_hosted."):
                raise RegistryError(f"{bid}: a mock backend cannot use a hosted adapter")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Registry":
        return cls(yaml.safe_load(Path(path).read_text()))

    def active_id(self, environ: Mapping[str, str] = os.environ) -> str:
        bid = environ.get("CNAI_LLM_BACKEND") or self.raw.get("active_backend")
        if bid not in self.backends:
            raise RegistryError(f"unknown active backend {bid!r}")
        return bid

    def config(self, backend_id: str) -> Dict[str, Any]:
        if backend_id not in self.backends:
            raise RegistryError(f"unknown backend {backend_id!r}")
        return {"backend_id": backend_id, **self.backends[backend_id]}

    def build(self, backend_id: str, *, profile: str = "synthetic-dev", environ: Mapping[str, str] = os.environ,
              transport: Optional[object] = None) -> Backend:
        cfg = self.config(backend_id)
        if cfg["mode"] == "hosted" and profile not in HOSTED_BUILD_PROFILES:
            raise BackendUnavailable("hosted adapters are not available in this build profile")
        mod_name, _, cls_name = cfg["adapter"].partition(":")
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
        except ImportError:
            raise BackendUnavailable("the adapter package is not present in this build") from None
        params = dict(cfg.get("params", {}))
        if cfg["mode"] == "mock":
            return cls(backend_id=backend_id, model_version=cfg.get("model_version", "1"), **params)
        secret = load_secret(cfg.get("api_key"), environ) if cfg.get("api_key") else None
        return cls(backend_id=backend_id, provider=cfg["provider"], model_id=cfg["model_id"], model_version=str(cfg["model_version"]),
                   endpoint=cfg["endpoint"], secret=secret, transport=transport, params=params,
                   endpoint_allowlist=tuple(cfg.get("endpoint_allowlist", ())))

"""Versioned prompt templates (ARCHITECTURE 10.2 rule 4). A template's text hash is recorded with every run."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
DATA_OPEN, DATA_CLOSE = "<<<DATA", "DATA>>>"


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    version: str
    system: str
    user: str
    sha256: str

    def render(self, data: Dict[str, Any], schema: Dict[str, Any]) -> tuple:
        blob = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        if DATA_CLOSE in blob or DATA_OPEN in blob:
            raise PromptError("data contains the prompt delimiter")
        user = self.user.replace("{data_json}", f"{DATA_OPEN}\n{blob}\n{DATA_CLOSE}").replace(
            "{schema_json}", json.dumps(schema, sort_keys=True, separators=(",", ":")))
        return self.system, user


def load_prompt(prompt_id: str, directory: Path = PROMPT_DIR) -> Prompt:
    p = directory / f"{prompt_id}.yaml"
    if not p.is_file():
        raise PromptError(f"unknown prompt {prompt_id!r}")
    raw = p.read_text()
    d = yaml.safe_load(raw)
    for k in ("prompt_id", "version", "system", "user"):
        if k not in d:
            raise PromptError(f"prompt {prompt_id} is missing {k}")
    if d["prompt_id"] != prompt_id:
        raise PromptError("prompt id does not match its file name")
    return Prompt(d["prompt_id"], str(d["version"]), d["system"].strip(), d["user"].strip(), hashlib.sha256(raw.encode()).hexdigest())

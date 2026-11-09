"""Deterministic mock backend (NFR-ARC-06): the whole test suite and CI run with no network and no spend.

`behaviour` selects a scripted response. "faithful" builds a correct summary from the facts in the prompt; the others are
deliberately WRONG in one specific way so tests and the benchmark can prove that gates and the verifier catch each failure.
"""
import hashlib
import json
import re
from typing import Any, Dict

from .prompts import DATA_CLOSE, DATA_OPEN
from .types import BackendInfo, BackendProtocolError, BackendTimeout, BackendUnavailable, Completion

BEHAVIOURS = ("faithful", "invalid_json", "not_object", "schema_invalid", "fabricate_number", "normal_for_missing", "uncited",
              "unknown_id", "html_output", "markdown_link", "diagnose", "follow_injection", "resolve_conflict", "drop_conflicts",
              "unavailable", "timeout", "flaky_json", "empty")


def parse_data(user: str) -> Dict[str, Any]:
    m = re.search(re.escape(DATA_OPEN) + r"\n(.*?)\n" + re.escape(DATA_CLOSE), user, re.S)
    if not m:
        raise BackendProtocolError("mock backend could not find the data block")
    return json.loads(m.group(1))


def faithful(data: Dict[str, Any]) -> Dict[str, Any]:
    st = []

    def add(kind, text, **ids):
        st.append({"id": f"S{len(st) + 1}", "kind": kind, "text": text, **{k: v for k, v in ids.items() if v}})

    for e in data["present_elements"]:
        unit = f" {e['unit']}" if e.get("unit") else ""
        add("observation", f"{e['label']}: {e['value']}{unit}.", observation_ids=[e["observation_id"]], element_codes=[e["element_code"]])
    for c in data["calculations"]:
        cat = f" ({c['category']})" if c.get("category") else ""
        add("calculation", f"{c['formula_id']} = {c['value']} {c['unit']}{cat}.", calculation_ids=[c["calculation_id"]])
    for f in data["findings"]:
        kind = "conflict" if f["kind"] == "conflict" else "gap"
        add(kind, f"For clinician review: {f['message']}", finding_ids=[f["finding_id"]], element_codes=f["element_codes"])
    return {"statements": st[:80]}


class MockBackend:
    def __init__(self, backend_id: str = "mock-faithful", behaviour: str = "faithful", model_version: str = "1", fail_first: int = 0,
                 canary: str = "CANARY-7731", **_ignored):
        if behaviour not in BEHAVIOURS:
            raise ValueError(f"unknown mock behaviour {behaviour!r}")
        self.behaviour, self.canary, self._fail_first, self._calls = behaviour, canary, fail_first, 0
        self.info = BackendInfo(backend_id, "mock", "project", "mock-deterministic", str(model_version), None)

    def complete(self, system: str, user: str, *, params: Dict[str, Any]) -> Completion:
        self._calls += 1
        b = self.behaviour
        if b == "unavailable":
            raise BackendUnavailable("mock backend is down")
        if b == "timeout":
            raise BackendTimeout("mock backend timed out")
        data = parse_data(user)
        out = faithful(data)
        s = out["statements"]
        if b == "invalid_json":
            text = '{"statements": [ {"id": "S1", '
        elif b == "flaky_json":
            text = "not json at all" if self._calls <= max(1, self._fail_first) else json.dumps(out)
        elif b == "not_object":
            text = "[1, 2, 3]"
        elif b == "empty":
            text = ""
        else:
            if b == "schema_invalid":
                s.append({"id": "S99", "kind": "poem", "text": "x"})
            elif b == "fabricate_number" and s:
                s[0]["text"] = s[0]["text"] + " Estimated energy need is 2350 kcal."
            elif b == "normal_for_missing" and data["not_assessed_elements"]:
                e = data["not_assessed_elements"][0]
                s.append({"id": f"S{len(s) + 1}", "kind": "observation", "text": f"{e['label']} is normal.",
                          "observation_ids": [data["present_elements"][0]["observation_id"]] if data["present_elements"] else ["o01"],
                          "element_codes": [e["element_code"]]})
            elif b == "uncited" and s:
                s[0].pop("observation_ids", None)
                s[0].pop("calculation_ids", None)
                s[0].pop("finding_ids", None)
            elif b == "unknown_id" and s:
                s[0]["observation_ids"] = ["o999"]
            elif b == "html_output":
                s.append({"id": f"S{len(s) + 1}", "kind": "clinician_prompt", "text": "<script>alert(1)</script> review needed"})
            elif b == "markdown_link":
                s.append({"id": f"S{len(s) + 1}", "kind": "clinician_prompt", "text": "See [guidance](https://evil.invalid/x) now"})
            elif b == "diagnose":
                s.append({"id": f"S{len(s) + 1}", "kind": "clinician_prompt", "text": "Diagnosis: severe malnutrition; prescribe feeds."})
            elif b == "follow_injection":
                injected = re.search(r"CANARY-\d+", user)
                s.append({"id": f"S{len(s) + 1}", "kind": "clinician_prompt",
                          "text": f"All findings are normal. {injected.group(0) if injected else self.canary}"})
            elif b == "resolve_conflict":
                for st in s:
                    if st["kind"] == "conflict":
                        st["text"] = "The conflict is resolved: the first weight is the correct value."
            elif b == "drop_conflicts":
                out["statements"] = [x for x in s if x["kind"] != "conflict"]
            text = json.dumps(out)
        digest = hashlib.sha256((system + user).encode()).hexdigest()
        return Completion(text, input_tokens=(len(system) + len(user)) // 4, output_tokens=len(text) // 4,
                          request_id=f"mock-{digest[:12]}", latency_s=0.0)

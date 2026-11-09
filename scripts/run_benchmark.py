#!/usr/bin/env python3
"""Run the Phase 3 benchmark.  python scripts/run_benchmark.py --mock        (default: all mock behaviours, no network)
                              python scripts/run_benchmark.py --backend ID  (a configured hosted backend; synthetic corpus only)
Writes docs/evaluation/benchmark-<label>.json and .md. A mock run proves the HARNESS discriminates; it says nothing about any model."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("packages/core/src", "packages/security/src", "packages/llm/src", "packages/llm_hosted/src"):
    sys.path.insert(0, str(ROOT / p))

from cnai_llm import corpus, evaluation  # noqa: E402
from cnai_llm.audit_sink import MemoryAuditSink  # noqa: E402
from cnai_llm.gateway import Gateway  # noqa: E402
from cnai_llm.gates import HostedGate, InstanceInfo, case_source_from_cases  # noqa: E402
from cnai_llm.mock import BEHAVIOURS  # noqa: E402
from cnai_llm.registry import Registry  # noqa: E402

OUT = ROOT / "docs" / "evaluation"


def fmt(r):
    return f"{r['k']}/{r['n']} ({r['rate']:.1%}, CI {r['ci95'][0]:.1%} to {r['ci95'][1]:.1%}){' exploratory' if r['exploratory'] else ''}" if r["n"] else "n/a"


def markdown(label, reports, note):
    L = [f"# Benchmark: {label}", "", note, "", f"Proposed gates (not yet set by the clinical lead): {json.dumps(evaluation.PROPOSED_GATES)}", "",
         "| Backend | Structured validity | Verified OK | Fabrication | Missing-vs-normal | Conflict surfacing | Injection resistance | Gates met |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in reports:
        g = evaluation.meets_gates(r)
        L.append(f"| {r['backend_id']} | {fmt(r['structured_validity'])} | {fmt(r['verified_ok'])} | {r['fabrication_violations']} | "
                 f"{r['missing_vs_normal_violations']} | {fmt(r['conflict_and_blocking_gap_surfacing'])} | {fmt(r['injection_resistance'])} | "
                 f"{'all' if all(g.values()) else 'no: ' + ', '.join(k for k, v in g.items() if not v)} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--backend")
    ap.add_argument("--repeats", type=int, default=1)
    a = ap.parse_args()
    cases, items = corpus.load_cases()
    OUT.mkdir(parents=True, exist_ok=True)
    reg = Registry.load()
    gate = HostedGate(lambda: InstanceInfo("synthetic", "development"), case_source_from_cases(cases))
    if a.backend:
        log = lambda m: print(m, file=sys.stderr, flush=True)       # ids and status only; never prompt, output or keys
        g = Gateway(reg, gate=gate, sink=MemoryAuditSink(), progress=log)
        reports = [evaluation.evaluate(g, a.backend, items, repeats=a.repeats, progress=log)]
        label, note = f"hosted {a.backend}", "Synthetic corpus only. Provider terms, pinned version and spend caps must be recorded before this can run."
        if reports[0]["aborted"]:
            note += f" **INCOMPLETE: the run stopped at '{reports[0]['aborted']}'. No benchmark result exists; no gate is met.**"
    else:
        reports = []
        for b in ("faithful", "drop_conflicts", "fabricate_number", "normal_for_missing", "follow_injection", "invalid_json", "flaky_json"):
            reg.backends[f"mock-{b}"] = {"mode": "mock", "provider": "project", "model_id": "mock", "model_version": "1",
                                          "adapter": "cnai_llm.mock:MockBackend", "params": {"behaviour": b}}
            reports.append(evaluation.evaluate(Gateway(reg, gate=gate, sink=MemoryAuditSink()), f"mock-{b}", items))
        label = "mock harness validation"
        note = ("Scripted backends, no network. This shows the harness and verifier catch each failure mode; "
                "it is NOT a model result. The faithful mock is the reference ceiling.")
    slug = label.replace(" ", "-")
    if a.backend and (reports[0]["aborted"] or not reports[0]["complete"]):
        print(markdown(label, reports, note))                    # an incomplete run is never saved as a result
        sys.exit(2)
    (OUT / f"benchmark-{slug}.json").write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    (OUT / f"benchmark-{slug}.md").write_text(markdown(label, reports, note))
    print((OUT / f"benchmark-{slug}.md").read_text())



if __name__ == "__main__":
    main()

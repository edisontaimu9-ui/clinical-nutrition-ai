"""Loads the synthetic adult cases and builds gateway inputs from the deterministic analysis."""
import json
from pathlib import Path
from typing import Dict, List, Tuple

from cnai.engine import analyse
from cnai.models import Case
from cnai.testing import catalogue, rules

from . import summary_task

DATA = Path(__file__).resolve().parents[4] / "data" / "synthetic" / "adult"


def load_cases() -> Tuple[Dict[str, Case], List[Tuple[str, dict]]]:
    """Returns ({case_id: Case}, [(case_id, inputs)]) for in-scope cases only (refused cases have nothing to summarise)."""
    cases, items = {}, []
    for p in sorted(DATA.glob("SYN-ADULT-*.json")):
        case = Case.from_dict(json.loads(p.read_text()))
        cases[case.case_id] = case
        report = analyse(case, catalogue(), rules())
        if report["scope"]["status"] == "in_scope":
            items.append((case.case_id, summary_task.build_inputs(case, report)))
    return cases, items

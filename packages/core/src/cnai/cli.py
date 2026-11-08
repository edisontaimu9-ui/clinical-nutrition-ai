"""Command line: analyse a synthetic case file and print the report.

    python -m cnai.cli analyse data/synthetic/adult/SYN-ADULT-001.json
"""
import json
import sys
from pathlib import Path

from .engine import analyse
from .models import Case, Catalogue, RuleSet


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "analyse":
        print("usage: python -m cnai.cli analyse <case.json>", file=sys.stderr)
        return 2
    case = Case.from_dict(json.loads(Path(argv[1]).read_text()))
    report = analyse(case, Catalogue.load(), RuleSet.load(case.pathway if case.pathway == "adult_general" else "adult_general"))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

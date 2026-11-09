#!/usr/bin/env python3
"""Run a backup and restore drill against a synthetic or throwaway database.

    DATABASE_URL=postgresql://.../cnai_dev python scripts/restore_drill.py --drill-db cnai_drill_restore
        [--key-file path]   base64 32-byte key, chmod 600; if omitted a temporary development key is used

Prints a JSON report and exits 1 if the drill finds a problem. See docs/ops/recovery_targets.yaml.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "security" / "src"))

from cnai_security.backup import DrillError, run_restore_drill  # noqa: E402
from cnai_security.crypto import LocalKeyProvider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill-db", default="cnai_drill_restore")
    ap.add_argument("--key-file")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required")
        return 2
    provider = LocalKeyProvider.from_file("drill", args.key_file) if args.key_file else LocalKeyProvider.generate("drill-temporary")
    try:
        rep = run_restore_drill(url, provider, drill_db=args.drill_db, targets_file=str(ROOT / "docs" / "ops" / "recovery_targets.yaml"))
    except DrillError as exc:
        print(f"drill refused: {exc}")
        return 2
    print(json.dumps(rep.__dict__, indent=2, default=str))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())

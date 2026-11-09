#!/usr/bin/env python3
"""Fail if the real-data profile could include a hosted adapter, or any real-data package imports cnai_llm_hosted."""
import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HOSTED = "cnai_llm_hosted"
PKG_DIRS = {"cnai": "core", "cnai_security": "security", "cnai_llm": "llm", "cnai_llm_hosted": "llm_hosted"}


def imports_hosted(path: Path) -> bool:
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == HOSTED for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == HOSTED:
            return True
    return False


def check() -> list:
    errs = []
    prof = yaml.safe_load((ROOT / "build/profiles/real-data.yaml").read_text())
    if HOSTED in prof.get("packages", []) or HOSTED not in prof.get("excluded_packages", []):
        errs.append("real-data profile must exclude cnai_llm_hosted")
    for pkg in prof.get("packages", []):
        for f in (ROOT / "packages" / PKG_DIRS[pkg] / "src" / pkg).rglob("*.py"):
            if imports_hosted(f):
                errs.append(f"{f.relative_to(ROOT)} imports {HOSTED}")
    return errs


if __name__ == "__main__":
    e = check()
    print("\n".join(e) if e else "build profiles OK")
    sys.exit(1 if e else 0)

#!/usr/bin/env python3
"""Documentation validation: files, links, requirement IDs, safety-rule coverage, mermaid, SQL parse, decisions,
traceability. Run from anywhere: python scripts/validate_docs.py [repo_root]"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
errors, warnings = [], []

required = [
    "README.md", "ARCHITECTURE.md", "REQUIREMENTS.md", "SECURITY.md", "CLINICAL_SAFETY.md",
    "docs/decisions/decision-log.md", "docs/ROADMAP.md", "docs/EVALUATION.md",
    "docs/data/schema-design.sql", ".gitignore", "docs/TRACEABILITY.md", "docs/THREAT_MODEL.md", "docs/ops/recovery_targets.yaml", "docs/clinical/rule-review-sheet.md",
    "docs/clinical/rule-signoff.md", ".github/workflows/ci.yml", "requirements-dev.txt",
    "packages/llm/config/backends.yaml", "build/profiles/real-data.yaml", "docs/evaluation/benchmark-mock-harness-validation.md",
]
for r in required:
    p = root / r
    if not p.exists() or p.stat().st_size == 0:
        errors.append(f"missing/empty: {r}")

SKIP = {".git", ".pytest_cache", "__pycache__", ".hypothesis", "node_modules"}


def visible(p):
    return not (set(p.relative_to(root).parts) & SKIP)


md_files = [p for p in root.rglob("*.md") if visible(p)]

# 1. Relative markdown links resolve
link_re = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(#[^)]*)?\)")
for f in md_files:
    for m in link_re.finditer(f.read_text()):
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (f.parent / target).resolve().exists():
            errors.append(f"broken link in {f.relative_to(root)}: {target}")

# 2. Requirement IDs: unique definitions; roadmap references exist
req_text = (root / "REQUIREMENTS.md").read_text()
defined = re.findall(r"^\| ((?:FR|NFR)-[A-Z]+-\d{2}) \|", req_text, re.M)
dups = {i for i in defined if defined.count(i) > 1}
if dups:
    errors.append(f"duplicate requirement IDs: {sorted(dups)}")
defined_set = set(defined)
road = (root / "docs/ROADMAP.md").read_text()
for m in re.finditer(r"((?:FR|NFR)-[A-Z]+-\d{2})((?:/\d{2})*)(?:…(\d{2}))?", road):
    base = m.group(1)
    ids = [base]
    prefix = base[:-2]
    for extra in re.findall(r"/(\d{2})", m.group(2) or ""):
        ids.append(prefix + extra)
    if m.group(3):
        lo, hi = int(base[-2:]), int(m.group(3))
        ids = [f"{prefix}{n:02d}" for n in range(lo, hi + 1)]
    for i in ids:
        if i not in defined_set:
            errors.append(f"roadmap references undefined requirement {i}")

# Requirements not scheduled in any roadmap phase (informational)
road_ids = set()
for m in re.finditer(r"((?:FR|NFR)-[A-Z]+-\d{2})((?:/\d{2})*)(?:…(\d{2}))?", road):
    base = m.group(1); prefix = base[:-2]
    road_ids.add(base)
    for extra in re.findall(r"/(\d{2})", m.group(2) or ""):
        road_ids.add(prefix + extra)
    if m.group(3):
        for n in range(int(base[-2:]), int(m.group(3)) + 1):
            road_ids.add(f"{prefix}{n:02d}")
unscheduled = sorted(defined_set - road_ids)
if unscheduled:
    warnings.append(f"requirements not named in roadmap exit/scope lists: {unscheduled}")

# 3. All 8 safety rules appear in README and in CLINICAL_SAFETY enforcement table
safety = (root / "CLINICAL_SAFETY.md").read_text()
rows = re.findall(r"^\| ([1-8]) \| ", safety, re.M)
if rows != [str(i) for i in range(1, 9)]:
    errors.append(f"CLINICAL_SAFETY §4 rule rows incomplete: {rows}")
readme = (root / "README.md").read_text()
n_readme = len(re.findall(r"^\d\. ", readme.split("Non-negotiable safety rules")[1].split("## Document map")[0], re.M))
if n_readme != 8:
    errors.append(f"README lists {n_readme} safety rules, expected 8")

# 4. The 10 tasks are mapped in README
for i in range(1, 11):
    if not re.search(rf"^\| {i}\. ", readme, re.M):
        errors.append(f"README document map missing task {i}")

# 5. Mermaid blocks fenced and non-empty
for f in md_files:
    txt = f.read_text()
    fences = txt.count("```")
    if fences % 2:
        errors.append(f"unbalanced code fences in {f.relative_to(root)}")
    for blk in re.findall(r"```mermaid\n(.*?)```", txt, re.S):
        if not blk.strip():
            errors.append(f"empty mermaid block in {f.relative_to(root)}")

# 6. Decision IDs in index match sections
dl = (root / "docs/decisions/decision-log.md").read_text()
idx = set(re.findall(r"^\| (D-\d{3}) \|", dl, re.M))
sec = set(re.findall(r"^## (D-\d{3}) ·", dl, re.M))
if idx != sec:
    errors.append(f"decision index/sections mismatch: {sorted(idx ^ sec)}")
for d in set(re.findall(r"D-\d{3}", "".join(p.read_text() for p in md_files))):
    if d not in idx:
        errors.append(f"reference to unknown decision {d}")

# 7. SQL parses
try:
    import pglast
    sql = (root / "docs/data/schema-design.sql").read_text()
    stmts = pglast.parse_sql(sql)
    print(f"SQL parsed OK: {len(stmts)} statements")
except ImportError:
    warnings.append("pglast not installed; SQL not parsed")
except Exception as e:  # noqa
    errors.append(f"SQL parse error: {e}")

# 8. No obviously real-looking identifiers / secrets
secret_re = re.compile(r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9]{20,})")
for f in root.rglob("*"):
    if f.is_file() and visible(f) and f.suffix in {".md", ".sql", ".yaml", ".yml", ".json", ".py"}:
        try:
            if secret_re.search(f.read_text()):
                errors.append(f"possible secret in {f.relative_to(root)}")
        except Exception:
            pass

# 9. Forbidden: Claude/co-author mentions in docs (user preference for commits; keep repo clean too)
for f in md_files:
    if re.search(r"co-authored-by", f.read_text(), re.I):
        errors.append(f"co-author trailer text in {f.relative_to(root)}")

# 10. Generated documents are current; traceability map is sound
import subprocess
for script in ("gen_traceability.py", "gen_review_sheet.py"):
    r = subprocess.run([sys.executable, str(root / "scripts" / script), "--check"], capture_output=True, text=True)
    if r.returncode:
        errors.append(f"{script} --check failed: {(r.stdout + r.stderr).strip()[:300]}")

# 11. The sign-off file must not claim approval that the rule file does not carry, and vice versa
signoff = (root / "docs/clinical/rule-signoff.md").read_text()
rules_status = re.search(r"^review_status: (\w+)", (root / "packages/core/rules/adult_general.yaml").read_text(), re.M).group(1)
if rules_status != "draft" and "**Not signed**" in signoff:
    errors.append(f"rule set status is {rules_status} but docs/clinical/rule-signoff.md is not signed")

print(f"requirements defined: {len(defined)}  decisions: {len(idx)}  md files: {len(md_files)}")
for w in warnings:
    print("WARN:", w)
for e in errors:
    print("ERROR:", e)
print("RESULT:", "FAIL" if errors else "PASS")
sys.exit(1 if errors else 0)

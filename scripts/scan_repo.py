#!/usr/bin/env python3
"""Repository scanner: the synthetic-only rule (D-009, D-020) and basic secret detection (NFR-SEC-06/07).

Two checks, both run in CI and both runnable locally:

  synthetic-only  Every JSON case under data/synthetic and db/seeds must be flagged synthetic, carry a pseudonym of
                  the form SYN-NNNN, contain no identifier-like field names, and no identifier-like text values.
                  Every text file in the repository is scanned for e-mail addresses, phone numbers and long numeric
                  identifiers.
  secrets         Common credential formats (cloud keys, private keys, API tokens, assignments of long literals to
                  names such as api_key or password).

Matches are reported as file:line with the value masked, so the scanner never reprints a secret into CI logs.

    python scripts/scan_repo.py [--root PATH] [--check synthetic-only|secrets|all]
Exit status 1 when anything is found.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com", "anthropic.com")
ALLOWED_EMAIL_LOCALPARTS = ("noreply",)

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
PHONE_INTL = re.compile(r"(?<![\w.])\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3}[\s-]?\d{3,4}\b")
PHONE_MW = re.compile(r"(?<![\w.])0(?:88|89|98|99|21|31|1)\d{7}\b")
LONG_ID = re.compile(r"(?<![\w.])\d{10,}(?![\w.])")

SECRET_PATTERNS = {
    "cloud access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "API token (sk-)": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "credential assignment": re.compile(
        r"""(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['"][^'"\s]{12,}['"]"""),
}

IDENTIFIER_FIELD_NAMES = {"name", "full_name", "first_name", "last_name", "surname", "phone", "mobile", "email",
                          "address", "dob", "date_of_birth", "birth_date", "national_id", "nrc", "passport",
                          "hospital_number", "mrn", "ssn"}
PSEUDONYM = re.compile(r"^SYN-\d{4}$")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", ".hypothesis"}
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".sql", ".py", ".txt", ".toml", ".ini", ".cfg", ".sh", ".js", ".ts"}
# Scanner self-test fixtures and this file's own patterns are exempt from the identifier scan only.
EXEMPT_FROM_IDENTIFIER_SCAN = {"scripts/scan_repo.py", "tests/safety/test_scanners.py"}
EXEMPT_FROM_SECRET_SCAN = {"scripts/scan_repo.py", "tests/safety/test_scanners.py"}


def iter_text_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS) and p.suffix in TEXT_SUFFIXES:
            yield p


def mask(text: str) -> str:
    return text[:2] + "***" + text[-1:] if len(text) > 4 else "***"


def scan_identifiers_in_text(rel: str, text: str):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in EMAIL.finditer(line):
            domain = m.group(1).lower()
            local = m.group(0).split("@")[0].lower()
            if domain.endswith(ALLOWED_EMAIL_DOMAINS) or local in ALLOWED_EMAIL_LOCALPARTS:
                continue
            out.append(f"{rel}:{n}: e-mail address {mask(m.group(0))}")
        for label, rx in (("international phone number", PHONE_INTL), ("local phone number", PHONE_MW),
                          ("long numeric identifier", LONG_ID)):
            for m in rx.finditer(line):
                out.append(f"{rel}:{n}: {label} {mask(m.group(0))}")
    return out


def walk_json(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_json(v, f"{path}.{k}")
            yield path + "." + k, k, v
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_json(v, f"{path}[{i}]")


def check_synthetic_case(rel: str, data) -> list:
    out = []
    if isinstance(data, dict) and "cases" in data and "case_id" not in data:        # index file
        if data.get("synthetic") is not True:
            out.append(f"{rel}: index is not flagged synthetic")
        return out
    if not isinstance(data, dict):
        return [f"{rel}: expected a JSON object"]
    if data.get("synthetic") is not True:
        out.append(f"{rel}: case is not flagged \"synthetic\": true")
    pseudonym = (data.get("patient") or {}).get("pseudonym", "")
    if not PSEUDONYM.match(str(pseudonym)):
        out.append(f"{rel}: pseudonym must look like SYN-0001")
    for _, key, _ in walk_json(data):
        if str(key).lower() in IDENTIFIER_FIELD_NAMES:
            out.append(f"{rel}: identifier-like field name '{key}'")
    for o in data.get("observations", []):
        if isinstance(o.get("value"), str):
            out += [m.replace(rel, rel + " (observation " + str(o.get("id")) + ")", 1)
                    for m in scan_identifiers_in_text(rel, o["value"])]
    return out


def check_synthetic_only(root: Path) -> list:
    problems = []
    for folder in ("data/synthetic", "db/seeds"):
        base = root / folder
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.name != ".gitkeep":
                rel = str(p.relative_to(root))
                if p.suffix != ".json":
                    problems.append(f"{rel}: only reviewed JSON synthetic files are allowed here")
                    continue
                try:
                    problems += check_synthetic_case(rel, json.loads(p.read_text()))
                except json.JSONDecodeError as exc:
                    problems.append(f"{rel}: invalid JSON ({exc.msg})")
    for p in iter_text_files(root):
        rel = str(p.relative_to(root))
        if rel in EXEMPT_FROM_IDENTIFIER_SCAN:
            continue
        problems += scan_identifiers_in_text(rel, p.read_text(errors="ignore"))
    return problems


def check_secrets(root: Path) -> list:
    problems = []
    for p in iter_text_files(root):
        rel = str(p.relative_to(root))
        if rel in EXEMPT_FROM_SECRET_SCAN:
            continue
        for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            for label, rx in SECRET_PATTERNS.items():
                if rx.search(line):
                    problems.append(f"{rel}:{n}: possible {label}")
    for forbidden in (".env",):
        for p in root.rglob(forbidden):
            if not (set(p.relative_to(root).parts) & SKIP_DIRS):
                problems.append(f"{p.relative_to(root)}: environment file must not be committed")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--check", choices=["synthetic-only", "secrets", "all"], default="all")
    args = ap.parse_args(argv)
    root = Path(args.root)
    problems = []
    if args.check in ("synthetic-only", "all"):
        problems += check_synthetic_only(root)
    if args.check in ("secrets", "all"):
        problems += check_secrets(root)
    for p in problems:
        print("FOUND:", p)
    print(f"scan complete: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

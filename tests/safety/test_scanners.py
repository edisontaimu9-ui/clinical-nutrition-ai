"""The synthetic-only and secret scanners must pass on the repository and must catch planted violations."""
import json
from pathlib import Path

import pytest

import scan_repo

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.safety


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


GOOD_CASE = {"case_id": "SYN-ADULT-900", "synthetic": True, "patient": {"pseudonym": "SYN-9000", "sex_at_birth": "male"},
             "observations": [{"id": "o1", "element": "anthro.weight_kg", "status": "present", "value": 70, "unit": "kg"}]}


def test_the_repository_is_clean():
    assert scan_repo.check_synthetic_only(ROOT) == []
    assert scan_repo.check_secrets(ROOT) == []


def test_a_well_formed_synthetic_case_passes(tmp_path):
    write(tmp_path, "data/synthetic/adult/SYN-ADULT-900.json", json.dumps(GOOD_CASE))
    assert scan_repo.check_synthetic_only(tmp_path) == []


@pytest.mark.parametrize("mutation,fragment", [
    (lambda c: c.update(synthetic=False), "not flagged"),
    (lambda c: c.pop("synthetic"), "not flagged"),
    (lambda c: c["patient"].update(pseudonym="A. Banda"), "pseudonym"),
    (lambda c: c["patient"].update(pseudonym="SYN-12"), "pseudonym"),
    (lambda c: c["patient"].update(name="Someone"), "identifier-like field name 'name'"),
    (lambda c: c["patient"].update(dob="1990-01-01"), "identifier-like field name 'dob'"),
    (lambda c: c.update(phone="x"), "identifier-like field name 'phone'"),
    (lambda c: c["observations"][0].update(element="clin.gi_symptoms", value="call me on +265 999 123 456"), "phone"),
    (lambda c: c["observations"][0].update(element="clin.gi_symptoms", value="mail jane.doe@gmail.com"), "e-mail"),
])
def test_planted_identifier_violations_are_caught(tmp_path, mutation, fragment):
    case = json.loads(json.dumps(GOOD_CASE))
    mutation(case)
    write(tmp_path, "data/synthetic/adult/SYN-ADULT-900.json", json.dumps(case))
    problems = scan_repo.check_synthetic_only(tmp_path)
    assert any(fragment in p for p in problems), problems


def test_non_json_files_in_synthetic_folders_are_rejected(tmp_path):
    write(tmp_path, "data/synthetic/adult/patients.csv", "a,b\n1,2\n")
    assert any("only reviewed JSON" in p for p in scan_repo.check_synthetic_only(tmp_path))


@pytest.mark.parametrize("text,fragment", [
    ("contact: jane.doe@gmail.com\n", "e-mail"),
    ("call +265 999 123 456 today\n", "phone"),
    ("call 0999123456 today\n", "phone"),
    ("id 1234567890123\n", "numeric identifier"),
])
def test_identifiers_in_any_text_file_are_caught(tmp_path, text, fragment):
    write(tmp_path, "docs/notes.md", text)
    assert any(fragment in p for p in scan_repo.check_synthetic_only(tmp_path))


def test_allowed_addresses_and_ordinary_numbers_do_not_trigger(tmp_path):
    write(tmp_path, "docs/notes.md", "Use noreply@anthropic.com or me@example.com. Version 0.1.0, 22.86 kg/m2, 2026-01-15.\n")
    assert scan_repo.check_synthetic_only(tmp_path) == []


@pytest.mark.parametrize("line,fragment", [
    ("key = 'AKIA" + "ABCDEFGHIJKLMNOP'\n", "cloud access key"),
    ("-----BEGIN " + "PRIVATE KEY-----\n", "private key"),
    ("token = 'sk-" + "abcdefghijklmnopqrstuvwx'\n", "API token"),
    ("api_key: \"" + "abcdefghijklmnop1234\"\n", "credential assignment"),
    ("gh_" + "token = 'ghp_" + "a" * 36 + "'\n", "GitHub token"),
])
def test_planted_secrets_are_caught_and_masked(tmp_path, line, fragment):
    write(tmp_path, "scripts/x.py", line)
    problems = scan_repo.check_secrets(tmp_path)
    assert any(fragment in p for p in problems), problems
    assert not any("abcdefghijklmnop" in p for p in problems), "scanner output must not reprint the secret"


def test_env_files_are_rejected(tmp_path):
    write(tmp_path, ".env", "X=1\n")
    assert any("environment file" in p for p in scan_repo.check_secrets(tmp_path))

"""Plain data structures and loaders. Immutable where it matters."""
import json
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[4]
SCHEMA_DIR = Path(os.environ.get("CNAI_SCHEMA_DIR", ROOT / "packages" / "schemas"))
RULES_DIR = Path(os.environ.get("CNAI_RULES_DIR", ROOT / "packages" / "core" / "rules"))

STATUSES = ("present", "not_assessed", "not_applicable", "unknown_or_unreliable", "declared_normal_by_clinician")


class InputError(ValueError):
    """A case or observation is structurally invalid."""


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise InputError(f"invalid date: {value!r}") from exc


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InputError("a boolean is not a number")
    try:
        d = Decimal(str(value))
    except InvalidOperation as exc:
        raise InputError(f"not a number: {value!r}") from exc
    if not d.is_finite():
        raise InputError(f"not a finite number: {value!r}")
    return d


@dataclass(frozen=True)
class Observation:
    observation_id: str
    element_code: str
    status: str
    value: Any = None                    # as entered: number, string or bool (None unless status == present)
    unit_entered: Optional[str] = None
    observed_on: Optional[date] = None
    supersedes_id: Optional[str] = None
    declared_by: Optional[str] = None
    order: int = 0                       # position in the input; deterministic tie-break only

    @staticmethod
    def from_dict(d: dict, order: int) -> "Observation":
        status = d.get("status", "not_assessed")
        if status not in STATUSES:
            raise InputError(f"unknown status {status!r} for observation {d.get('id')}")
        if "id" not in d or "element" not in d:
            raise InputError("an observation needs 'id' and 'element'")
        value = d.get("value")
        if status != "present" and value is not None:
            raise InputError(f"observation {d['id']}: a value is allowed only when status is 'present'")
        if status == "present" and value is None:
            raise InputError(f"observation {d['id']}: status 'present' requires a value")
        if status != "present" and d.get("unit"):
            raise InputError(f"observation {d['id']}: a unit is allowed only when status is 'present'")
        if status == "declared_normal_by_clinician" and not d.get("declared_by"):
            raise InputError(f"observation {d['id']}: declared_normal_by_clinician requires declared_by")
        return Observation(
            observation_id=str(d["id"]), element_code=d["element"], status=status, value=value,
            unit_entered=d.get("unit"), observed_on=parse_date(d.get("observed_on")),
            supersedes_id=d.get("supersedes"), declared_by=d.get("declared_by"), order=order)


@dataclass(frozen=True)
class Case:
    case_id: str
    synthetic: bool
    pseudonym: str
    sex_at_birth: str
    setting: str
    pathway: str
    age_months: int
    assessment_date: date
    observations: tuple
    expected: dict = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Case":
        try:
            enc, pat = d["encounter"], d["patient"]
            obs = tuple(Observation.from_dict(o, i) for i, o in enumerate(d.get("observations", [])))
            ids = [o.observation_id for o in obs]
            if len(ids) != len(set(ids)):
                raise InputError("duplicate observation ids")
            if enc["setting"] not in ("inpatient", "outpatient", "community"):
                raise InputError(f"unknown setting {enc['setting']!r}")
            return Case(case_id=d["case_id"], synthetic=bool(d.get("synthetic", False)), pseudonym=pat["pseudonym"],
                        sex_at_birth=pat.get("sex_at_birth", "unknown"), setting=enc["setting"],
                        pathway=enc["pathway"], age_months=int(enc["age_months"]),
                        assessment_date=parse_date(enc["assessment_date"]), observations=obs,
                        expected=d.get("expected", {}))
        except KeyError as exc:
            raise InputError(f"missing field {exc}") from exc


@dataclass(frozen=True)
class ElementDef:
    code: str
    domain: str
    label: str
    value_type: str
    canonical_unit: Optional[str]
    plausible_min: Optional[Decimal]
    plausible_max: Optional[Decimal]
    staleness_days: Optional[int]
    conflict_tolerance: Optional[Decimal]
    allowed_values: Optional[tuple]
    enabled: bool
    pathways: tuple
    undated_ok: bool = False


@dataclass(frozen=True)
class Pathway:
    code: str
    label: str
    status: str
    min_age_months: int
    max_age_months: Optional[int]


@dataclass(frozen=True)
class Catalogue:
    version: str
    review_status: str
    elements: dict
    pathways: dict
    pathways_version: str

    @staticmethod
    def load(schema_dir: Path = SCHEMA_DIR) -> "Catalogue":
        els = json.loads((schema_dir / "data_elements.json").read_text())
        pws = json.loads((schema_dir / "pathways.json").read_text())
        dec = lambda v: None if v is None else Decimal(str(v))
        elements = {}
        for e in els["elements"]:
            elements[e["element_code"]] = ElementDef(
                code=e["element_code"], domain=e["domain"], label=e["label"], value_type=e["value_type"],
                canonical_unit=e.get("canonical_unit"), plausible_min=dec(e.get("plausible_min")),
                plausible_max=dec(e.get("plausible_max")), staleness_days=e.get("staleness_days"),
                conflict_tolerance=dec(e.get("conflict_tolerance")),
                allowed_values=tuple(e["allowed_values"]) if e.get("allowed_values") else None,
                enabled=e.get("enabled", True), pathways=tuple(e.get("pathways", [])),
                undated_ok=e.get("undated_ok", False))
        pathways = {p["pathway_code"]: Pathway(p["pathway_code"], p["label"], p["status"], p["min_age_months"],
                                              p["max_age_months"]) for p in pws["pathways"]}
        return Catalogue(els["version"], els["review_status"], elements, pathways, pws["version"])


@dataclass(frozen=True)
class RuleSet:
    rule_set: str
    version: str
    review_status: str
    pathway: str
    raw: dict

    @staticmethod
    def load(pathway: str = "adult_general", rules_dir: Path = RULES_DIR) -> "RuleSet":
        raw = yaml.safe_load((rules_dir / f"{pathway}.yaml").read_text())
        return RuleSet(raw["rule_set"], str(raw["rule_set_version"]), raw["review_status"], raw["pathway"], raw)

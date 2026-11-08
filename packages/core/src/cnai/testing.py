"""Small builders for tests and examples. Not used by the engine."""
from .engine import analyse
from .models import Case, Catalogue, RuleSet

_CAT = None
_RULES = None


def catalogue() -> Catalogue:
    global _CAT
    if _CAT is None:
        _CAT = Catalogue.load()
    return _CAT


def rules() -> RuleSet:
    global _RULES
    if _RULES is None:
        _RULES = RuleSet.load("adult_general")
    return _RULES


def o(oid, element, value=None, unit=None, on="2026-01-15", status=None, **extra):
    """Build one observation dict. status defaults to 'present' when a value is given, else 'not_assessed'."""
    d = {"id": oid, "element": element, "status": status or ("present" if value is not None else "not_assessed")}
    if value is not None:
        d["value"] = value
    if unit is not None:
        d["unit"] = unit
    if on is not None:
        d["observed_on"] = on
    d.update(extra)
    return d


def make_case(observations=(), *, sex="male", setting="inpatient", pathway="adult_general", age_months=480,
              date="2026-01-15", synthetic=True, case_id="TEST-1"):
    return Case.from_dict({
        "case_id": case_id, "synthetic": synthetic,
        "patient": {"pseudonym": "SYN-9999", "sex_at_birth": sex},
        "encounter": {"setting": setting, "pathway": pathway, "age_months": age_months, "assessment_date": date},
        "observations": list(observations),
    })


def run(case_or_obs, **kw):
    case = case_or_obs if isinstance(case_or_obs, Case) else make_case(case_or_obs, **kw)
    return analyse(case, catalogue(), rules())


def findings(report, kind=None, element=None):
    return [f for f in report["findings"]
            if (kind is None or f["kind"] == kind) and (element is None or element in f["element_codes"])]

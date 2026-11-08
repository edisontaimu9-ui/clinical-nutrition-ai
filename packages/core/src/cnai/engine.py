"""Analysis engine: scope guard, element states, implausible values, conflicts, staleness, gaps, calculations.

Safety properties enforced here (and tested in tests/safety):
  * Absence is `not_assessed`, never normal (FR-STATE-02).
  * Out-of-scope cases (planned pathway, age outside the band, pregnancy or lactation) are refused, not handled
    "best effort" (FR-POP-02).
  * Conflicts are reported with all records involved and never auto-resolved (FR-CONF-01).
  * Numbers in the output are either copied from recorded observations or computed by deterministic calculators.
  * The report always says the clinical logic has not been independently reviewed while the rule set is not
    `independently_reviewed` (FR-GOV-04).
"""
import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from . import calculators
from .models import Case, Catalogue, ElementDef, InputError, Observation, RuleSet, parse_decimal
from .units import UnitError, plain, to_canonical
from .views import Eff, View

REPORT_SCHEMA = "cnai.analysis/1"
CRIT_ORDER = {"blocking": 0, "important": 1, "optional": 2}
KIND_ORDER = {"conflict": 0, "implausible_value": 1, "stale_value": 2, "gap": 3, "red_flag": 4}


class DataClassError(RuntimeError):
    """A non-synthetic case was offered to a synthetic-only instance (D-020)."""


def banner(rules: RuleSet) -> str:
    if rules.review_status == "independently_reviewed":
        return "Rule set independently reviewed. Output is a draft for clinician review."
    return ("Clinical logic not independently reviewed (rule set status: %s). Output is a draft for clinician review only."
            % rules.review_status)


# ---------------------------------------------------------------------------- scope ---
def _scope_refusal(case: Case, cat: Catalogue) -> Optional[list]:
    pw = cat.pathways.get(case.pathway)
    if pw is None:
        return [f"pathway '{case.pathway}' is not recognised"]
    if pw.status != "active":
        return [f"pathway '{case.pathway}' is {pw.status} and is not available; no analysis is produced"]
    if case.age_months < pw.min_age_months or (pw.max_age_months is not None and case.age_months > pw.max_age_months):
        upper = "no upper limit" if pw.max_age_months is None else f"{pw.max_age_months} months"
        return [f"age {case.age_months} months is outside the {case.pathway} band ({pw.min_age_months} months to {upper}); "
                "no analysis is produced"]
    return None


# ----------------------------------------------------------------------- validation ---
def _validate(o: Observation, el: ElementDef, case: Case) -> Eff:
    """Return the effective record for one live observation."""
    if o.status != "present":
        return Eff(o, o.status)
    problems = []
    canonical = conversion = None
    if el.value_type == "numeric":
        try:
            value = parse_decimal(o.value)
            conversion = to_canonical(el.code, value, o.unit_entered, el.canonical_unit)
            canonical = conversion.canonical
            if el.plausible_min is not None and canonical < el.plausible_min:
                problems.append(f"{plain(canonical)} {el.canonical_unit} is below the plausible range "
                                f"({el.plausible_min} to {el.plausible_max}); check units and entry")
            if el.plausible_max is not None and canonical > el.plausible_max:
                problems.append(f"{plain(canonical)} {el.canonical_unit} is above the plausible range "
                                f"({el.plausible_min} to {el.plausible_max}); check units and entry")
        except (InputError, UnitError) as exc:
            problems.append(str(exc))
    elif el.value_type == "boolean":
        if not isinstance(o.value, bool):
            problems.append("a true/false value is required")
    else:
        if not isinstance(o.value, str) or not o.value.strip():
            problems.append("a text value is required")
        elif el.value_type == "coded" and o.value not in el.allowed_values:
            problems.append(f"'{o.value}' is not an allowed value (allowed: {', '.join(el.allowed_values)})")
    if o.observed_on is not None and o.observed_on > case.assessment_date:
        problems.append(f"observation date {o.observed_on.isoformat()} is after the assessment date")
    if problems:
        return Eff(o, "unknown_or_unreliable", None, conversion, tuple(problems))
    return Eff(o, "present", canonical, conversion)


def _same(a: Eff, b: Eff, el: ElementDef) -> bool:
    if a.eff_status != b.eff_status:
        return False
    if a.eff_status != "present":
        return True
    if a.canonical is not None and b.canonical is not None:
        return abs(a.canonical - b.canonical) <= (el.conflict_tolerance or Decimal(0))
    return a.obs.value == b.obs.value


def _build_view(el: ElementDef, live: list, case: Case) -> View:
    view = View(element=el)
    if not live:
        return view
    view.implicit = False
    view.effs = [_validate(o, el, case) for o in live]
    by_day = defaultdict(list)
    for e in view.effs:
        by_day[e.day].append(e)
    for day, group in by_day.items():
        if any(not _same(group[0], other, el) for other in group[1:]):
            view.conflict_days.append(day)
    latest_day = max(by_day)
    latest = by_day[latest_day]
    if latest_day in view.conflict_days:
        view.state = "conflicting"
        view.selected = None
    else:
        view.selected = max(latest, key=lambda e: e.obs.order)
        view.state = view.selected.eff_status
    return view


# ------------------------------------------------------------------------- findings ---
class _Findings:
    def __init__(self):
        self.items = []

    def add(self, kind, rule, criticality, elements, observations, message):
        self.items.append({
            "kind": kind, "rule_id": rule["id"], "rule_version": str(rule["version"]), "criticality": criticality,
            "element_codes": sorted(elements), "observation_ids": sorted(observations), "message": message})

    def sorted(self):
        out = sorted(self.items, key=lambda f: (CRIT_ORDER[f["criticality"]], KIND_ORDER[f["kind"]], f["rule_id"],
                                                f["element_codes"], f["observation_ids"]))
        for f in out:
            f["finding_id"] = "{}:{}:{}".format(f["rule_id"], ",".join(f["element_codes"]), ",".join(f["observation_ids"]))
        return out


def _describe(e: Eff) -> str:
    o = e.obs
    if o.status != "present":
        return f"{o.status} ({o.observation_id})"
    unit = f" {o.unit_entered}" if o.unit_entered else ""
    return f"{o.value}{unit} ({o.observation_id})"


def _applies(rule: dict, case: Case) -> bool:
    cond = rule.get("applies_when") or {}
    if "sex_at_birth" in cond and case.sex_at_birth not in cond["sex_at_birth"]:
        return False
    if "setting" in cond and case.setting not in cond["setting"]:
        return False
    return True


def _pair_side_true(view: Optional[View], side: dict) -> bool:
    if view is None or view.state != "present" or view.selected is None:
        return False
    v = view.selected
    if "equals" in side:
        return v.obs.value is side["equals"] or v.obs.value == side["equals"]
    if "greater_than" in side:
        return v.canonical is not None and v.canonical > Decimal(str(side["greater_than"]))
    return False


# ------------------------------------------------------------------------- the engine ---
def analyse(case: Case, catalogue: Catalogue, rules: RuleSet, data_class: str = "synthetic") -> dict:
    """Analyse one case and return a JSON-serialisable report. Raises DataClassError in a synthetic instance if the
    case is not flagged synthetic."""
    if data_class == "synthetic" and not case.synthetic:
        raise DataClassError("this instance holds synthetic data only; the case is not flagged synthetic")
    raw = rules.raw
    report = {
        "schema": REPORT_SCHEMA, "case_id": case.case_id, "pseudonym": case.pseudonym,
        "pathway": case.pathway, "assessment_date": case.assessment_date.isoformat(),
        "rule_set": {"id": rules.rule_set, "version": rules.version, "review_status": rules.review_status},
        "catalogue": {"version": catalogue.version, "review_status": catalogue.review_status},
        "banner": banner(rules),
    }

    refusal = _scope_refusal(case, catalogue)
    if refusal:
        return _finish(report, {"status": "refused", "reasons": refusal, "rule_id": None})

    # applicable elements
    applicable = {c: e for c, e in catalogue.elements.items()
                  if e.enabled and (not e.pathways or case.pathway in e.pathways)}
    input_errors = []
    superseded = set()
    by_code = {c: [] for c in applicable}
    ids = {o.observation_id: o for o in case.observations}
    for o in case.observations:
        if o.element_code not in catalogue.elements:
            input_errors.append({"observation_id": o.observation_id, "error": f"unknown data element '{o.element_code}'"})
        elif o.element_code not in applicable:
            input_errors.append({"observation_id": o.observation_id,
                                 "error": f"data element '{o.element_code}' is disabled or does not apply to this pathway"})
        else:
            by_code[o.element_code].append(o)
        if o.supersedes_id:
            target = ids.get(o.supersedes_id)
            if target is None or target.element_code != o.element_code:
                input_errors.append({"observation_id": o.observation_id,
                                     "error": "supersedes an observation that does not exist or belongs to another element"})
            else:
                superseded.add(o.supersedes_id)

    views = {c: _build_view(applicable[c], [o for o in obs if o.observation_id not in superseded], case)
             for c, obs in by_code.items()}

    # scope exclusions that depend on recorded data (pregnancy, lactation)
    for rule in raw.get("scope_exclusions", []):
        view = views.get(rule["element"])
        if view and view.state == "present" and view.selected.obs.value in rule["refuse_if_value_in"]:
            return _finish(report, {"status": "refused", "reasons": [rule["message"]], "rule_id": rule["id"]})

    findings = _Findings()

    # implausible values
    imp = raw["implausible"]
    for view in views.values():
        for e in view.effs:
            if e.problems:
                findings.add("implausible_value", imp, imp["criticality"], [view.element.code], [e.obs.observation_id],
                             f"{view.element.label}: {'; '.join(e.problems)}. The value is treated as unreliable and is "
                             "not used in calculations.")

    # conflicts: same element, same day
    cnf = raw["conflicts"]["same_day_disagreement"]
    for view in views.values():
        for day in sorted(view.conflict_days):
            group = [e for e in view.effs if e.day == day]
            crit = cnf["criticality_by_element"].get(view.element.code, cnf["default_criticality"])
            when = "on an undated record" if day == date.min else f"dated {day.isoformat()}"
            findings.add("conflict", cnf, crit, [view.element.code], [e.obs.observation_id for e in group],
                         f"{view.element.label}: conflicting records {when}: "
                         + " versus ".join(_describe(e) for e in group) + ". " + cnf["message"])
    # conflicts: pairs of elements
    for pair in raw["conflicts"].get("pairs", []):
        va, vb = views.get(pair["a"]["element"]), views.get(pair["b"]["element"])
        if _pair_side_true(va, pair["a"]) and _pair_side_true(vb, pair["b"]):
            findings.add("conflict", pair, pair["criticality"], [va.element.code, vb.element.code],
                         [va.selected.obs.observation_id, vb.selected.obs.observation_id],
                         f"{va.element.label} versus {vb.element.label}: {pair['message']}")

    # staleness
    stl = raw["staleness"]
    for view in views.values():
        sel = view.selected
        if sel is None or sel.eff_status not in ("present", "declared_normal_by_clinician"):
            continue
        if sel.obs.observed_on is None:
            if view.element.undated_ok:
                continue
            findings.add("stale_value", stl, stl["undated_criticality"], [view.element.code], [sel.obs.observation_id],
                         f"{view.element.label}: {stl['message_undated']}")
        elif view.element.staleness_days is not None:
            age = (case.assessment_date - sel.obs.observed_on).days
            if age > view.element.staleness_days:
                findings.add("stale_value", stl, stl["criticality"], [view.element.code], [sel.obs.observation_id],
                             f"{view.element.label}: {stl['message_old']} Recorded {age} days before the assessment "
                             f"(window: {view.element.staleness_days} days).")

    # completeness gaps
    for rule in raw["completeness"]:
        if not _applies(rule, case):
            continue
        view = views.get(rule["element"])
        if view is None:
            continue
        if view.state == "not_assessed":
            status_text = "has not been assessed" if view.implicit else "is recorded as not assessed"
        elif view.state == "unknown_or_unreliable":
            status_text = "is recorded as unknown or unreliable"
        else:
            continue
        findings.add("gap", rule, rule["criticality"], [view.element.code],
                     [e.obs.observation_id for e in view.effs],
                     f"{view.element.label} {status_text}. {rule['why']}")

    results, calc_refusals = calculators.compute_all(views)

    states = {}
    for code in sorted(views):
        v = views[code]
        entry = {"state": v.state, "recorded": not v.implicit, "label": v.element.label}
        if v.selected is not None:
            o = v.selected.obs
            entry.update({"observation_id": o.observation_id, "observed_on": o.observed_on.isoformat() if o.observed_on else None})
            if o.status == "present":
                entry.update({"entered_value": str(o.value), "unit_entered": o.unit_entered})
                if v.selected.canonical is not None:
                    entry["canonical_value"] = plain(v.selected.canonical)
                    entry["canonical_unit"] = v.element.canonical_unit
                    entry["converted"] = bool(v.selected.conversion and v.selected.conversion.converted)
            if o.status == "declared_normal_by_clinician":
                entry["declared_by"] = o.declared_by
        elif v.state == "conflicting":
            entry["observation_ids"] = sorted(e.obs.observation_id for e in v.effs if e.day == max(x.day for x in v.effs))
        states[code] = entry

    report.update({
        "scope": {"status": "in_scope", "reasons": [], "rule_id": None},
        "element_states": states,
        "calculations": results,
        "calculation_refusals": calc_refusals,
        "findings": findings.sorted(),
        "input_errors": sorted(input_errors, key=lambda x: x["observation_id"]),
    })
    return _finish(report)


def _finish(report: dict, scope: Optional[dict] = None) -> dict:
    if scope is not None:
        report["scope"] = scope
    digest_source = json.dumps(report, sort_keys=True, default=str, ensure_ascii=True)
    report["report_sha256"] = hashlib.sha256(digest_source.encode()).hexdigest()
    return report

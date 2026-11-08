"""Unit handling (FR-CALC-03, NFR-UX-05): explicit, recorded conversions; no silent assumptions.

A numeric value must arrive with the unit it was entered in. Conversion factors are exact or standard
molar-mass based and defined per data element, because the same unit string can mean different things for
different analytes. An unrecognised or missing unit is an error, never a guess.
"""
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Optional

# element_code -> {normalised entered unit: (multiply, divide)}   canonical = value * multiply / divide
_KG = {"kg": (D(1), D(1)), "lb": (D("0.45359237"), D(1)), "lbs": (D("0.45359237"), D(1)), "g": (D("0.001"), D(1))}
_CM = {"cm": (D(1), D(1)), "m": (D(100), D(1)), "in": (D("2.54"), D(1)), "mm": (D(1), D(10))}

CONVERSIONS: dict[str, dict[str, tuple[D, D]]] = {
    "anthro.weight_kg": _KG,
    "anthro.usual_weight_kg": _KG,
    "func.handgrip_strength_kg": {"kg": (D(1), D(1)), "lb": (D("0.45359237"), D(1)), "lbs": (D("0.45359237"), D(1))},
    "anthro.height_cm": _CM,
    "anthro.muac_cm": {"cm": (D(1), D(1)), "mm": (D(1), D(10)), "in": (D("2.54"), D(1))},
    "bio.haemoglobin_g_dl": {"g/dl": (D(1), D(1)), "g/l": (D(1), D(10))},
    # glucose: 1 mmol/L = 18.0156 mg/dL
    "bio.glucose_mmol_l": {"mmol/l": (D(1), D(1)), "mg/dl": (D(1), D("18.0156"))},
    # creatinine: 1 mg/dL = 88.4 umol/L
    "bio.creatinine_umol_l": {"umol/l": (D(1), D(1)), "mg/dl": (D("88.4"), D(1))},
    # potassium is monovalent: 1 mEq/L = 1 mmol/L
    "bio.potassium_mmol_l": {"mmol/l": (D(1), D(1)), "meq/l": (D(1), D(1))},
    # phosphate (as phosphorus): 1 mg/dL = 0.3229 mmol/L
    "bio.phosphate_mmol_l": {"mmol/l": (D(1), D(1)), "mg/dl": (D("0.3229"), D(1))},
    # magnesium: 1 mg/dL = 0.4114 mmol/L
    "bio.magnesium_mmol_l": {"mmol/l": (D(1), D(1)), "mg/dl": (D("0.4114"), D(1))},
    "intake.oral_intake_percent_usual": {"%": (D(1), D(1)), "percent": (D(1), D(1))},
}


def plain(d: D) -> str:
    """Decimal as a plain string without exponent notation and without trailing zeros (70, not 7E+1)."""
    text = format(d.normalize(), "f")
    return text


class UnitError(ValueError):
    """Raised when a unit is missing, unrecognised, or not valid for the element."""


@dataclass(frozen=True)
class Converted:
    canonical: D
    entered_unit: str
    canonical_unit: str
    converted: bool          # True when a factor other than 1 was applied (reported, never silent)


def normalise_unit(unit: str) -> str:
    return unit.strip().lower().replace("µ", "u").replace("μ", "u").replace(" ", "")


def to_canonical(element_code: str, value: D, unit_entered: Optional[str], canonical_unit: str) -> Converted:
    table = CONVERSIONS.get(element_code)
    if table is None:
        raise UnitError(f"no unit conversions are defined for {element_code}")
    if unit_entered is None or not str(unit_entered).strip():
        raise UnitError(f"a unit is required for {element_code}; none is assumed")
    key = normalise_unit(unit_entered)
    if key not in table:
        accepted = ", ".join(sorted(table))
        raise UnitError(f"unit '{unit_entered}' is not accepted for {element_code} (accepted: {accepted})")
    mul, div = table[key]
    return Converted(canonical=value * mul / div, entered_unit=unit_entered.strip(), canonical_unit=canonical_unit,
                     converted=(mul != div))

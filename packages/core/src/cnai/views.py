"""Per-element view of a case: the effective state of each data element after validation and conflict handling."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from .models import ElementDef, Observation
from .units import Converted


@dataclass(frozen=True)
class Eff:
    """One live observation after validation. eff_status may differ from the recorded status when the value is
    unusable (implausible, wrong unit, future date): such a value is treated as 'unknown_or_unreliable'."""
    obs: Observation
    eff_status: str
    canonical: Optional[Decimal] = None          # numeric elements only
    conversion: Optional[Converted] = None
    problems: tuple = ()

    @property
    def day(self) -> date:
        return self.obs.observed_on or date.min

    def signature(self):
        if self.eff_status == "present":
            return (self.eff_status, self.canonical if self.canonical is not None else str(self.obs.value))
        return (self.eff_status, None)


@dataclass
class View:
    element: ElementDef
    state: str = "not_assessed"                  # one of the five states, or 'conflicting'
    implicit: bool = True                        # True when no observation exists at all (absence)
    selected: Optional[Eff] = None
    effs: list = field(default_factory=list)     # all live observations, validated
    conflict_days: list = field(default_factory=list)

    def usable(self):
        """Valid, present, numeric observations sorted oldest to newest (ties by input order)."""
        rows = [e for e in self.effs if e.eff_status == "present" and e.canonical is not None]
        return sorted(rows, key=lambda e: (e.day, e.obs.order))

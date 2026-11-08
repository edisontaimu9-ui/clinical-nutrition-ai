"""Deterministic core of the Clinical Nutrition AI Agent (Phase 1, adult pathway).

No LLM, no network, no database access: pure functions over a case and versioned reference data.
Every number in an output traces to a recorded observation; absence is never treated as normal.
"""

__version__ = "0.1.0"

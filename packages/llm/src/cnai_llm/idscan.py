"""Identifier-pattern scan on outbound prompts (SECURITY.md 7.3 item 5). Defence in depth only: the data-class and
per-request gates are the real controls. Reports categories and counts, never the matched text."""
import re
from collections import Counter
from typing import Dict, Iterable

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_PSEUDONYM = re.compile(r"\bSYN-[A-Z0-9-]+\b")
PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"\bhttps?://\S+", re.I),
    "phone": re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,}\d(?![\w.])"),
    "long_number": re.compile(r"\b\d{9,}\b"),
    "id_like_code": re.compile(r"\b(?=[A-Z0-9]{8,12}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,12}\b"),
    "labelled_name": re.compile(r"\b(?:patient name|full name|name of patient|next of kin)\s*[:=]", re.I),
    "labelled_dob": re.compile(r"\b(?:date of birth|dob|born on)\s*[:=]?\s*\d", re.I),
    "address_label": re.compile(r"\b(?:home address|residential address|village|physical address)\s*[:=]", re.I),
}


def scan(text: str, deny_names: Iterable[str] = ()) -> Dict[str, int]:
    """Return {category: count} for anything that looks like a direct identifier."""
    cleaned = _PSEUDONYM.sub(" ", _ISO_DATE.sub(" ", text))
    found: Counter = Counter()
    for cat, rx in PATTERNS.items():
        n = len(rx.findall(cleaned))
        if n:
            found[cat] = n
    low = cleaned.lower()
    for name in deny_names:
        n = low.count(name.lower())
        if n and name.strip():
            found["deny_listed_name"] += n
    return dict(found)

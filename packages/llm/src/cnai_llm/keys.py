"""API key handling (NFR-SEC-10, SECURITY.md 7.3 item 7). Keys come from an environment variable or a permission-restricted
file OUTSIDE the repository; they are wrapped so they cannot be printed or logged by accident."""
import os
import stat
from pathlib import Path
from typing import Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[4]


class KeyError_(Exception):
    """A key could not be loaded safely. Never includes key material."""


class Secret:
    __slots__ = ("_v", "source")

    def __init__(self, value: str, source: str):
        self._v, self.source = value, source

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return f"Secret(<redacted>, source={self.source})"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("Secret cannot be pickled")

    def __eq__(self, other):
        return False        # never compare secrets by value in application code

    __hash__ = None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_secret(spec: Optional[Mapping], environ: Mapping = os.environ, repo_root: Path = REPO_ROOT) -> Secret:
    if not spec:
        raise KeyError_("no key source is configured")
    if "env" in spec:
        v = environ.get(spec["env"], "")
        if v.strip():
            return Secret(v.strip(), f"env:{spec['env']}")
        if "file" not in spec:
            raise KeyError_(f"environment variable {spec['env']} is not set")
        # not set: fall through to the key file, if one is configured
    if "file" in spec:
        p = Path(os.path.expanduser(spec["file"]))
        if _inside(p, repo_root):
            raise KeyError_("the key file is inside the repository; keep it outside")
        if not p.is_file():
            raise KeyError_("the key file does not exist")
        if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise KeyError_("the key file must not be readable by group or others (chmod 600)")
        v = p.read_text().strip()
        if not v:
            raise KeyError_("the key file is empty")
        if len(v.split()) != 1 or not v.isprintable():
            raise KeyError_("the key file must contain only the key (found extra text); run scripts/set_groq_key.py to rewrite it")
        return Secret(v, f"file:{p.name}")
    raise KeyError_("unsupported key source (use env or file)")

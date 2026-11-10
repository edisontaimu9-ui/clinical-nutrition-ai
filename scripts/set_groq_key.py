#!/usr/bin/env python3
"""Save a Groq API key to ~/.config/cnai/groq_key (mode 600) without it ever appearing on screen, in shell history or in Git.
Run it alone, as ONE command:   python scripts/set_groq_key.py
Paste the key at the hidden prompt (nothing is shown while you paste), then press Enter. The key is never printed."""
import getpass
import os
import re
import sys
from pathlib import Path

TARGET = Path(os.path.expanduser("~/.config/cnai/groq_key"))
SHAPE = re.compile(r"^gsk_[A-Za-z0-9]{20,100}$")


def main() -> int:
    raw = getpass.getpass("Paste your Groq key (hidden), then press Enter: ")
    key = raw.strip()
    if not SHAPE.match(key):
        # Never echo what was typed. Say only what is structurally wrong.
        print(f"That does not look like a single Groq key (it should start with 'gsk_' and contain only letters and digits; "
              f"you entered {len(raw)} characters, {len(raw.split())} word(s)). Nothing was saved. Try again.")
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chmod(TARGET, 0o600)
    print(f"Saved {len(key)} characters to {TARGET} (mode 600). The key was not displayed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

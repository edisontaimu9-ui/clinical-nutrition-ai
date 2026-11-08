#!/usr/bin/env python3
"""Write (or check) the synthetic adult cases in data/synthetic/adult/.

    python scripts/generate_synthetic.py           # write
    python scripts/generate_synthetic.py --check   # exit 1 if committed files differ from the generator
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "core" / "src"))

from cnai import synthetic  # noqa: E402


def main() -> int:
    if "--check" in sys.argv:
        problems = synthetic.check()
        for p in problems:
            print("DRIFT:", p)
        print("synthetic cases are up to date" if not problems else "run scripts/generate_synthetic.py")
        return 1 if problems else 0
    names = synthetic.write_all()
    print(f"wrote {len(names)} synthetic cases to {synthetic.SYNTHETIC_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

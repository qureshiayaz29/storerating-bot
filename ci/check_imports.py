"""Fail when any Python file outside job/sources/ imports a scraping library.

All store access must go through job/sources/registry.py (PLAN.md → Store Source).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DIR = ROOT / "job" / "sources"
FORBIDDEN = ("google_play_scraper", "google_play_scraper_api")
PATTERN = re.compile(r"^\s*(?:import|from)\s+(" + "|".join(FORBIDDEN) + r")\b", re.M)
SKIP_PARTS = {".venv", "venv", "node_modules", ".git", "__pycache__"}


def offenders(root: Path = ROOT) -> list[str]:
    found = []
    for path in root.rglob("*.py"):
        if SKIP_PARTS & set(path.parts):
            continue
        if ALLOWED_DIR in path.parents:
            continue
        for m in PATTERN.finditer(path.read_text(errors="ignore")):
            line = path.read_text(errors="ignore")[: m.start()].count("\n") + 1
            found.append(f"{path.relative_to(root)}:{line}: imports {m.group(1)}")
    return found


if __name__ == "__main__":
    bad = offenders()
    for b in bad:
        print(b)
    if bad:
        print("Store access must go through job/sources/registry.py.", file=sys.stderr)
        sys.exit(1)
    print("OK: no scraper imports outside job/sources/")

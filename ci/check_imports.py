"""Fail when code outside job/sources/ or web/src/sources/ imports a scraping library.

All store access must go through job/sources/registry.py (Python) or web/src/sources/ (TypeScript) (PLAN.md → Store Source).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DIR = ROOT / "job" / "sources"
WEB_SRC = ROOT / "web" / "src"
WEB_ALLOWED_DIR = WEB_SRC / "sources"
TS_PATTERN = re.compile(r"""(?:from\s+|import\s*\(\s*)['"]google-play-scraper['"]""")
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
    web_src = root / "web" / "src"
    if web_src.exists():
        for path in list(web_src.rglob("*.ts")) + list(web_src.rglob("*.astro")):
            if (web_src / "sources") in path.parents:
                continue
            text = path.read_text(errors="ignore")
            for m in TS_PATTERN.finditer(text):
                line = text[: m.start()].count("\n") + 1
                found.append(f"{path.relative_to(root)}:{line}: imports google-play-scraper")
    return found


if __name__ == "__main__":
    bad = offenders()
    for b in bad:
        print(b)
    if bad:
        print("Store access must go through job/sources/registry.py or web/src/sources/.", file=sys.stderr)
        sys.exit(1)
    print("OK: no scraper imports outside job/sources/ and web/src/sources/")

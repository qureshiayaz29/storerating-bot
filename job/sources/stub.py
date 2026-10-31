"""StubAdapter — serves Listings and Reviews straight from Contract Fixtures.

Used in tests and to prove the Daily Job runs end to end with
STORE_SOURCE=stub and no other code change.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import CONTRACTS_DIR, Listing, Review, SourceError, validate_listing, validate_review

FIXTURES = CONTRACTS_DIR / "fixtures" / "google_play_scraper"


class StubAdapter:
    name = "stub"

    def __init__(self, fixtures_dir: Path = FIXTURES) -> None:
        self._cases: dict[str, Path] = {}
        for case in sorted(p for p in fixtures_dir.iterdir() if p.is_dir()):
            meta = json.loads((case / "case.json").read_text())
            self._cases[meta["package"]] = case

    def _case(self, package: str) -> Path:
        case = self._cases.get(package)
        if case is None or not (case / "expected_listing.json").exists():
            raise SourceError("not_found", package, "no stub fixture")
        return case

    def get_listing(self, package: str, country: str = "us") -> Listing:
        data = json.loads((self._case(package) / "expected_listing.json").read_text())
        return validate_listing(Listing(**data))

    def get_reviews(self, package: str, since_id: str | None = None, limit: int = 500,
                    country: str = "us") -> list[Review]:
        rows = json.loads((self._case(package) / "expected_reviews.json").read_text())
        out = []
        for row in rows:
            if row["id"] == since_id:
                break
            out.append(validate_review(Review(**row), package))
            if len(out) >= limit:
                break
        return out

    def exists(self, package: str, country: str = "us") -> bool:
        try:
            self._case(package)
            return True
        except SourceError:
            return False

"""Store Source contract. See PLAN.md → Terms → Store Source.

Every adapter returns Listing and Review objects validated against the JSON
Schemas in contracts/, and raises only SourceError. Job code never imports a
scraping library; it asks registry.py for the Active Source.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from jsonschema import Draft202012Validator, FormatChecker

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

Reason = Literal["not_found", "rate_limited", "blocked", "parse_failed", "unavailable"]
REASONS: tuple[Reason, ...] = ("not_found", "rate_limited", "blocked", "parse_failed", "unavailable")


class SourceError(Exception):
    """The only exception a Store Source adapter may raise."""

    def __init__(self, reason: Reason, package: str, detail: str = "") -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown SourceError reason: {reason}")
        self.reason = reason
        self.package = package
        self.detail = detail
        super().__init__(f"{reason}: {package}" + (f" ({detail})" if detail else ""))

    @property
    def fallback_worthy(self) -> bool:
        """True when trying the Fallback Source makes sense."""
        return self.reason in ("blocked", "rate_limited", "parse_failed")


@dataclass(frozen=True)
class Listing:
    store: str
    package: str
    name: str
    developer: str | None
    icon_url: str | None
    version: str | None
    rating: float | None
    review_count: int | None
    rating_histogram: list[int] | None
    installs_tier: str | None
    size: str | None
    updated_at: str | None
    release_notes: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def one_star_share(self) -> float | None:
        h = self.rating_histogram
        if not h or sum(h) == 0:
            return None
        return round(h[0] / sum(h), 4)


@dataclass(frozen=True)
class Review:
    id: str
    stars: int
    text: str
    thumbs_up: int
    app_version: str | None
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@runtime_checkable
class StoreSource(Protocol):
    name: str

    def get_listing(self, package: str) -> Listing: ...

    def get_reviews(self, package: str, since_id: str | None = None, limit: int = 500) -> list[Review]:
        """Newest first. Stops at since_id (exclusive) or after limit reviews."""
        ...

    def exists(self, package: str) -> bool: ...


@lru_cache(maxsize=None)
def _validator(schema_name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS_DIR / schema_name).read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_listing(listing: Listing) -> Listing:
    _raise_if_invalid("listing.schema.json", listing.to_dict(), listing.package)
    return listing


def validate_review(review: Review, package: str) -> Review:
    _raise_if_invalid("review.schema.json", review.to_dict(), package)
    return review


def _raise_if_invalid(schema_name: str, data: dict, package: str) -> None:
    errors = sorted(_validator(schema_name).iter_errors(data), key=lambda e: e.path)
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise SourceError("parse_failed", package, f"{schema_name} {where}: {first.message}")

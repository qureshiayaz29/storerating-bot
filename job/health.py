"""Store Source health check: fetch a known big App and make sure the fields the
bot depends on are present. Catches Google page changes before users notice."""

from __future__ import annotations

from .sources.base import SourceError, StoreSource

CANARY = "com.whatsapp"


def check(source: StoreSource, package: str = CANARY) -> tuple[bool, str]:
    try:
        listing = source.get_listing(package)
    except SourceError as e:
        return False, f"{source.name} health check failed for {package}: {e.reason} ({e.detail[:200]})"
    missing = [f for f in ("name", "rating", "review_count", "rating_histogram") if getattr(listing, f) in (None, "", [])]
    if missing:
        return False, f"{source.name} health check: {package} is missing {', '.join(missing)}"
    try:
        if not source.get_reviews(package, limit=5):
            return False, f"{source.name} health check: no reviews returned for {package}"
    except SourceError as e:
        return False, f"{source.name} health check: reviews failed for {package}: {e.reason}"
    return True, f"{source.name} OK: {listing.name} {listing.rating}★, {listing.review_count} ratings"

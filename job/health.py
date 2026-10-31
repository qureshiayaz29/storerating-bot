"""Store Source health check: fetch a known big App and make sure the fields the
bot depends on are present. Catches Google page changes before users notice."""

from __future__ import annotations

from .sources.base import SourceError, StoreSource

CANARY = "play:us:com.whatsapp"
APP_STORE_CANARY = "ios:us:310633997"   # WhatsApp Messenger


def check(source: StoreSource, package: str = CANARY) -> tuple[bool, str]:
    name = source.name_for(package) if hasattr(source, "name_for") else source.name
    try:
        listing = source.get_listing(package)
    except SourceError as e:
        return False, f"{name} health check failed for {package}: {e.reason} ({e.detail[:200]})"
    required = ("name", "rating", "review_count") + (("rating_histogram",) if listing.store == "google_play" else ())
    missing = [f for f in required if getattr(listing, f) in (None, "", [])]
    if missing:
        return False, f"{name} health check: {package} is missing {', '.join(missing)}"
    try:
        if not source.get_reviews(package, limit=5):
            return False, f"{name} health check: no reviews returned for {package}"
    except SourceError as e:
        return False, f"{name} health check: reviews failed for {package}: {e.reason}"
    return True, f"{name} OK: {listing.name} {listing.rating}★, {listing.review_count} ratings"

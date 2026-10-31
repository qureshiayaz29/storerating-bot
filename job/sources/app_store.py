"""AppStoreAdapter — Source Adapter for Apple's App Store, on Apple's public endpoints:

- Listing: the iTunes Lookup API, https://itunes.apple.com/lookup?id=<track id>&country=<cc>
- Reviews: the customer reviews RSS feed (JSON), newest first, 10 pages of 50.

No keys, no scraping library. Ratings and reviews are per country (storefront).
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from .base import Listing, Review, SourceError, validate_listing, validate_review

LOOKUP = "https://itunes.apple.com/lookup?id={id}&country={cc}&entity=software"
REVIEWS = "https://itunes.apple.com/{cc}/rss/customerreviews/page={page}/id={id}/sortby=mostrecent/json"
MAX_PAGES = 10
UA = "StoreRating/1.0 (+https://github.com/qureshiayaz29/storerating-bot)"

HttpGet = Callable[[str], bytes]   # returns the body; raises urllib.error.HTTPError / URLError


def _default_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


class AppStoreAdapter:
    name = "itunes_api"

    def __init__(self, http_get: HttpGet | None = None, country: str = "us") -> None:
        self._get = http_get or _default_get
        self._country = country

    # --- contract -------------------------------------------------------

    def get_listing(self, package: str, country: str | None = None) -> Listing:
        cc = country or self._country
        data = self._json(package, LOOKUP.format(id=package, cc=cc))
        results = data.get("results") or []
        app = next((r for r in results if str(r.get("trackId")) == str(package)), None)
        if not results or app is None:
            raise SourceError("not_found", package, f"not on the App Store in {cc.upper()}")
        try:
            listing = Listing(
                store="app_store",
                package=str(app["trackId"]),
                name=app["trackName"],
                developer=app.get("sellerName") or app.get("artistName"),
                icon_url=app.get("artworkUrl512") or app.get("artworkUrl100"),
                version=app.get("version") or None,
                rating=_rating(app.get("averageUserRating")),
                review_count=_int(app.get("userRatingCount")),
                rating_histogram=None,          # Apple does not publish a star histogram
                installs_tier=None,             # nor install counts
                size=_size(app.get("fileSizeBytes")),
                updated_at=_iso(app.get("currentVersionReleaseDate")),
                release_notes=app.get("releaseNotes") or None,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError("parse_failed", package, f"{type(e).__name__}: {e}") from e
        return validate_listing(listing)

    def get_reviews(self, package: str, since_id: str | None = None, limit: int = 500,
                    country: str | None = None) -> list[Review]:
        cc = country or self._country
        out: list[Review] = []
        for page in range(1, MAX_PAGES + 1):
            try:
                data = self._json(package, REVIEWS.format(cc=cc, page=page, id=package))
            except SourceError as e:
                if e.reason == "not_found":   # past the last page, or no reviews in this country
                    break
                raise
            entries = (data.get("feed") or {}).get("entry") or []
            if isinstance(entries, dict):     # a single entry comes as an object
                entries = [entries]
            reviews = [e for e in entries if "im:rating" in e]   # older feeds put app info first
            if not reviews:
                break
            for raw in reviews:
                review = self._review(package, raw)
                if since_id is not None and review.id == since_id:
                    return out
                out.append(review)
                if len(out) >= limit:
                    return out
        return out

    def exists(self, package: str, country: str | None = None) -> bool:
        try:
            self.get_listing(package, country)
            return True
        except SourceError as e:
            if e.reason == "not_found":
                return False
            raise

    # --- helpers --------------------------------------------------------

    def _json(self, package: str, url: str) -> dict:
        try:
            body = self._get(url)
        except urllib.error.HTTPError as e:
            reason = {404: "not_found", 429: "rate_limited", 403: "blocked"}.get(e.code, "unavailable")
            raise SourceError(reason, package, f"HTTP {e.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            raise SourceError("unavailable", package, f"{type(e).__name__}: {e}") from None
        try:
            return json.loads(body or b"{}")
        except ValueError as e:
            raise SourceError("parse_failed", package, f"bad JSON: {e}") from None

    def _review(self, package: str, raw: dict) -> Review:
        try:
            title = _label(raw.get("title")).strip()
            content = _label(raw.get("content")).strip()
            text = f"{title}. {content}" if title and content and title != content else (content or title)
            review = Review(
                id=_label(raw["id"]),
                stars=int(_label(raw["im:rating"])),
                text=text,
                thumbs_up=int(_label(raw.get("im:voteCount")) or 0),
                app_version=_label(raw.get("im:version")) or None,
                created_at=_iso(_label(raw["updated"])),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError("parse_failed", package, f"review {type(e).__name__}: {e}") from e
        return validate_review(review, package)


def _label(node) -> str:
    if isinstance(node, dict):
        return str(node.get("label", ""))
    return "" if node is None else str(node)


def _rating(v) -> float | None:
    return None if v is None else round(float(v), 2)


def _int(v) -> int | None:
    return None if v is None else int(v)


def _size(v) -> str | None:
    if not v:
        return None
    mb = int(v) / 1_000_000
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{round(mb)} MB"


def _iso(v: str | None) -> str | None:
    if not v:
        return None
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

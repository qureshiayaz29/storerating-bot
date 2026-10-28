"""GooglePlayScraperAdapter — first Source Adapter, built on JoMingyu's
google-play-scraper. This is the only module allowed to import it
(enforced by ci/check_imports.py).
"""

from __future__ import annotations

import urllib.error
from datetime import datetime, timezone
from typing import Any, Callable

from .base import Listing, Review, SourceError, validate_listing, validate_review

AppFn = Callable[..., dict]
ReviewsFn = Callable[..., tuple[list[dict], Any]]

PAGE_SIZE = 200


def _default_fns() -> tuple[AppFn, ReviewsFn, Any]:
    import google_play_scraper as gps  # the one allowed import

    return gps.app, gps.reviews, gps.Sort.NEWEST


class GooglePlayScraperAdapter:
    name = "google_play_scraper"

    def __init__(
        self,
        app_fn: AppFn | None = None,
        reviews_fn: ReviewsFn | None = None,
        lang: str = "en",
        country: str = "us",
    ) -> None:
        if app_fn is None or reviews_fn is None:
            default_app, default_reviews, self._newest = _default_fns()
            app_fn = app_fn or default_app
            reviews_fn = reviews_fn or default_reviews
        else:
            self._newest = "NEWEST"
        self._app = app_fn
        self._reviews = reviews_fn
        self._lang = lang
        self._country = country

    # --- contract -------------------------------------------------------

    def get_listing(self, package: str) -> Listing:
        raw = self._call(package, self._app, package, lang=self._lang, country=self._country)
        try:
            listing = Listing(
                store="google_play",
                package=raw.get("appId") or package,
                name=raw["title"],
                developer=raw.get("developer"),
                icon_url=raw.get("icon"),
                version=_version(raw.get("version")),
                rating=_rating(raw.get("score")),
                review_count=_int(raw.get("ratings")),
                rating_histogram=_histogram(raw.get("histogram")),
                installs_tier=raw.get("installs"),
                size=raw.get("size") or None,
                updated_at=_iso_from_unix(raw.get("updated")),
                release_notes=raw.get("recentChanges") or None,
            )
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError("parse_failed", package, f"{type(e).__name__}: {e}") from e
        return validate_listing(listing)

    def get_reviews(self, package: str, since_id: str | None = None, limit: int = 500) -> list[Review]:
        out: list[Review] = []
        token = None
        while len(out) < limit:
            if token is None:
                page, token = self._call(
                    package, self._reviews, package,
                    lang=self._lang, country=self._country,
                    sort=self._newest, count=min(PAGE_SIZE, limit - len(out)),
                )
            else:
                page, token = self._call(package, self._reviews, package, continuation_token=token)
            if not page:
                break
            for raw in page:
                if since_id is not None and raw.get("reviewId") == since_id:
                    return out
                out.append(self._review(package, raw))
                if len(out) >= limit:
                    break
            if token is None or getattr(token, "token", "") is None:
                break
        return out

    def exists(self, package: str) -> bool:
        try:
            self.get_listing(package)
            return True
        except SourceError as e:
            if e.reason == "not_found":
                return False
            raise

    # --- helpers --------------------------------------------------------

    def _review(self, package: str, raw: dict) -> Review:
        try:
            review = Review(
                id=str(raw["reviewId"]),
                stars=int(raw["score"]),
                text=raw.get("content") or "",
                thumbs_up=int(raw.get("thumbsUpCount") or 0),
                app_version=raw.get("appVersion") or raw.get("reviewCreatedVersion") or None,
                created_at=_iso(raw["at"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise SourceError("parse_failed", package, f"review {type(e).__name__}: {e}") from e
        return validate_review(review, package)

    @staticmethod
    def _call(package: str, fn: Callable, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SourceError:
            raise
        except Exception as e:  # map every library or network error to a reason
            raise SourceError(_reason(e), package, f"{type(e).__name__}: {e}"[:300]) from e


def _reason(e: Exception) -> str:
    name = type(e).__name__
    text = str(e)
    if name == "NotFoundError":
        return "not_found"
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
    else:
        code = next((c for c in (429, 403, 404) if str(c) in text), None)
    if code == 429:
        return "rate_limited"
    if code == 403 or "Forbidden" in text:
        return "blocked"
    if code == 404:
        return "not_found"
    if isinstance(e, (KeyError, IndexError, TypeError, ValueError, AttributeError)):
        return "parse_failed"
    if name == "ExtraHTTPError":
        return "unavailable"
    return "unavailable"


def _int(v: Any) -> int | None:
    return int(v) if v is not None else None


def _rating(v: Any) -> float | None:
    return round(float(v), 2) if v is not None else None


def _version(v: Any) -> str | None:
    if not v or v == "Varies with device":
        return v or None
    return str(v)


def _histogram(v: Any) -> list[int] | None:
    if not v or len(v) != 5:
        return None
    return [int(x or 0) for x in v]


def _iso_from_unix(v: Any) -> str | None:
    if v is None:
        return None
    return datetime.fromtimestamp(int(v), tz=timezone.utc).isoformat()


def _iso(v: Any) -> str:
    """Return UTC ISO 8601.

    google-play-scraper builds review times with datetime.fromtimestamp(), so
    they arrive naive in the machine's local time. astimezone() on a naive
    datetime interprets it as local time, which is exactly that.
    """
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, str):
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            raise ValueError(f"naive datetime string is ambiguous: {v!r}")
        return dt.astimezone(timezone.utc).isoformat()
    raise TypeError(f"unsupported datetime value: {v!r}")

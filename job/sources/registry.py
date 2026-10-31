"""The only door to store data. Job code imports this module, never an adapter
library. STORE_SOURCE picks the Google Play Active Source, STORE_SOURCE_FALLBACK
its optional Fallback Source, APP_STORE_SOURCE the App Store adapter.

Job code works with App Keys (`play:in:com.whatsapp`, `ios:us:310633997`). The
KeyRouter sends each key to its store's adapter with the store ID and country.
"""

from __future__ import annotations

import os
from typing import Callable

from .base import Listing, Review, SourceError, StoreSource, parse_app_key

_ADAPTERS: dict[str, Callable[[], StoreSource]] = {}


def register(name: str, factory: Callable[[], StoreSource]) -> None:
    _ADAPTERS[name] = factory


def _google_play_scraper() -> StoreSource:
    from .google_play import GooglePlayScraperAdapter

    return GooglePlayScraperAdapter()


def _stub() -> StoreSource:
    from .stub import StubAdapter

    return StubAdapter()


def _itunes_api() -> StoreSource:
    from .app_store import AppStoreAdapter

    return AppStoreAdapter()


register("google_play_scraper", _google_play_scraper)
register("itunes_api", _itunes_api)
register("stub", _stub)

DEFAULT = "google_play_scraper"
APP_STORE_DEFAULT = "itunes_api"


class KeyRouter:
    """A StoreSource over App Keys: routes each call to the adapter for that key's store."""

    def __init__(self, play: StoreSource | None, app_store: StoreSource | None) -> None:
        self._by_store = {"google_play": play, "app_store": app_store}
        self.name = getattr(play, "name", None) or getattr(app_store, "name", "none")

    def adapter_for(self, key: str) -> StoreSource:
        source = self._by_store[parse_app_key(key).store]
        if source is None:
            raise SourceError("unavailable", key, f"no {parse_app_key(key).store} source configured")
        return source

    def name_for(self, key: str) -> str:
        return self.adapter_for(key).name

    def get_listing(self, key: str, country: str | None = None) -> Listing:
        k = parse_app_key(key)
        return self.adapter_for(key).get_listing(k.store_id, country=country or k.country)

    def get_reviews(self, key: str, since_id: str | None = None, limit: int = 500,
                    country: str | None = None) -> list[Review]:
        k = parse_app_key(key)
        return self.adapter_for(key).get_reviews(k.store_id, since_id=since_id, limit=limit,
                                                 country=country or k.country)

    def exists(self, key: str, country: str | None = None) -> bool:
        k = parse_app_key(key)
        return self.adapter_for(key).exists(k.store_id, country=country or k.country)


def available() -> list[str]:
    return sorted(_ADAPTERS)


def build(name: str) -> StoreSource:
    try:
        factory = _ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown Store Source adapter {name!r}; available: {', '.join(available())}") from None
    return factory()


def active_source() -> KeyRouter:
    return KeyRouter(build(os.environ.get("STORE_SOURCE") or DEFAULT),
                     build(os.environ.get("APP_STORE_SOURCE") or APP_STORE_DEFAULT))


def fallback_source() -> KeyRouter | None:
    name = os.environ.get("STORE_SOURCE_FALLBACK")
    if not name or name == (os.environ.get("STORE_SOURCE") or DEFAULT):
        return None
    return KeyRouter(build(name), None)

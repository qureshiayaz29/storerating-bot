"""The only door to store data. Job code imports this module, never an adapter
library. STORE_SOURCE picks the Active Source; STORE_SOURCE_FALLBACK the
optional Fallback Source.
"""

from __future__ import annotations

import os
from typing import Callable

from .base import StoreSource

_ADAPTERS: dict[str, Callable[[], StoreSource]] = {}


def register(name: str, factory: Callable[[], StoreSource]) -> None:
    _ADAPTERS[name] = factory


def _google_play_scraper() -> StoreSource:
    from .google_play import GooglePlayScraperAdapter

    return GooglePlayScraperAdapter()


def _stub() -> StoreSource:
    from .stub import StubAdapter

    return StubAdapter()


register("google_play_scraper", _google_play_scraper)
register("stub", _stub)

DEFAULT = "google_play_scraper"


def available() -> list[str]:
    return sorted(_ADAPTERS)


def build(name: str) -> StoreSource:
    try:
        factory = _ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown Store Source adapter {name!r}; available: {', '.join(available())}") from None
    return factory()


def active_source() -> StoreSource:
    return build(os.environ.get("STORE_SOURCE") or DEFAULT)


def fallback_source() -> StoreSource | None:
    name = os.environ.get("STORE_SOURCE_FALLBACK")
    if not name or name == (os.environ.get("STORE_SOURCE") or DEFAULT):
        return None
    return build(name)

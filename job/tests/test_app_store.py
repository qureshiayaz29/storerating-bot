"""App Store contract tests, App Keys and routing by store."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from job.sources import registry
from job.sources.app_store import AppStoreAdapter
from job.sources.base import CONTRACTS_DIR, SourceError, parse_app_key

FIXTURES = CONTRACTS_DIR / "fixtures" / "app_store"
CASES = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
OK_CASES = [c for c in CASES if (c / "expected_listing.json").exists()]


def load(case: Path, name: str):
    return json.loads((case / name).read_text())


class FakeApple:
    """Replays a fixture case: the Lookup API, then RSS pages; 404 past the last page."""

    def __init__(self, case: Path) -> None:
        self.case, self.urls = case, []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "/lookup?" in url:
            return (self.case / "lookup.json").read_bytes()
        page = int(url.split("/page=")[1].split("/")[0])
        pages = load(self.case, "reviews_pages.json") if (self.case / "reviews_pages.json").exists() else []
        if page > len(pages):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        return json.dumps(pages[page - 1]).encode()


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c.name)
def test_listing_and_reviews_match_the_contract(case):
    meta = load(case, "case.json")
    fake = FakeApple(case)
    a = AppStoreAdapter(http_get=fake)
    assert a.get_listing(meta["package"], country=meta["country"]).to_dict() == load(case, "expected_listing.json")
    assert [r.to_dict() for r in a.get_reviews(meta["package"], country=meta["country"])] == load(case, "expected_reviews.json")
    assert f"country={meta['country']}" in fake.urls[0] and f"/{meta['country']}/rss/" in fake.urls[1]


def test_reviews_stop_at_since_id_and_limit():
    case = FIXTURES / "large"
    a = AppStoreAdapter(http_get=FakeApple(case))
    assert [r.id for r in a.get_reviews("310633997", since_id="11000000002", country="in")] == ["11000000001"]
    assert len(a.get_reviews("310633997", limit=2, country="in")) == 2


def test_missing_app_is_not_found():
    a = AppStoreAdapter(http_get=FakeApple(FIXTURES / "removed"))
    with pytest.raises(SourceError) as e:
        a.get_listing("999999999", country="in")
    assert e.value.reason == "not_found" and "IN" in e.value.detail
    assert a.exists("999999999", country="in") is False


@pytest.mark.parametrize("code,reason", [(429, "rate_limited"), (403, "blocked"), (500, "unavailable")])
def test_http_errors_map_to_reasons(code, reason):
    def get(url):
        raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
    with pytest.raises(SourceError) as e:
        AppStoreAdapter(http_get=get).get_listing("1", country="us")
    assert e.value.reason == reason


def test_bad_json_and_network_errors():
    with pytest.raises(SourceError) as e:
        AppStoreAdapter(http_get=lambda url: b"<html>").get_listing("1")
    assert e.value.reason == "parse_failed"

    def down(url):
        raise urllib.error.URLError("timed out")
    with pytest.raises(SourceError) as e:
        AppStoreAdapter(http_get=down).get_listing("1")
    assert e.value.reason == "unavailable"


def test_app_keys():
    k = parse_app_key("ios:in:310633997")
    assert (k.store, k.country, k.store_id, str(k), k.badge) == ("app_store", "in", "310633997", "ios:in:310633997",
                                                               "App Store · IN")
    k = parse_app_key("play:gb:com.whatsapp")
    assert (k.store, k.country, k.store_id, k.badge) == ("google_play", "gb", "com.whatsapp", "Google Play · GB")
    assert parse_app_key("com.whatsapp") == parse_app_key("play:us:com.whatsapp")   # legacy keys were US Play


def test_router_sends_each_key_to_its_store_with_country():
    calls = []

    class Spy:
        def __init__(self, name):
            self.name = name

        def get_listing(self, package, country=None):
            calls.append((self.name, package, country))

    router = registry.KeyRouter(Spy("play"), Spy("apple"))
    router.get_listing("play:in:com.zerodha.kite3")
    router.get_listing("ios:us:310633997")
    router.get_listing("com.legacy.app")
    assert calls == [("play", "com.zerodha.kite3", "in"), ("apple", "310633997", "us"), ("play", "com.legacy.app", "us")]
    assert router.name_for("ios:us:1") == "apple"
    with pytest.raises(SourceError):
        registry.KeyRouter(Spy("play"), None).get_listing("ios:us:1")


def test_registry_builds_both_stores(monkeypatch):
    monkeypatch.setenv("STORE_SOURCE", "stub")
    monkeypatch.delenv("APP_STORE_SOURCE", raising=False)
    r = registry.active_source()
    assert r.name_for("play:us:x.y") == "stub" and r.name_for("ios:us:1") == "itunes_api"

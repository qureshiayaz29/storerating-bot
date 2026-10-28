"""Contract tests: every Store Source adapter must turn Contract Fixtures into
exactly the expected Listing and Reviews, and fail only with SourceError."""

from __future__ import annotations

import json
import time
import urllib.error
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from job.sources import registry
from job.sources.base import CONTRACTS_DIR, Listing, SourceError, validate_listing
from job.sources.google_play import GooglePlayScraperAdapter
from job.sources.stub import StubAdapter

FIXTURES = CONTRACTS_DIR / "fixtures" / "google_play_scraper"
CASES = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
OK_CASES = [c for c in CASES if (c / "expected_listing.json").exists()]
ERROR_CASES = [c for c in CASES if "raises" in json.loads((c / "case.json").read_text())]


def load(case: Path, name: str):
    return json.loads((case / name).read_text())


class NotFoundError(Exception):
    """Same class name as google_play_scraper.exceptions.NotFoundError."""


class FakeLibrary:
    """Replays a fixture case through the same call shapes as google-play-scraper."""

    def __init__(self, case: Path) -> None:
        self.meta = load(case, "case.json")
        self.case = case
        self.calls: list[dict] = []

    def app(self, package, **kwargs):
        self.calls.append({"fn": "app", **kwargs})
        if self.meta.get("raises") == "NotFoundError":
            raise NotFoundError("App not found(404).")
        return load(self.case, "app.json")

    def reviews(self, package, continuation_token=None, **kwargs):
        self.calls.append({"fn": "reviews", **kwargs})
        pages = load(self.case, "reviews_pages.json")
        i = 0 if continuation_token is None else continuation_token.page
        nxt = SimpleNamespace(page=i + 1, token="t") if i + 1 < len(pages) else None
        return [self._as_library(r) for r in pages[i]], nxt

    @staticmethod
    def _as_library(raw: dict) -> dict:
        # Fixtures store UTC strings; the real library returns naive local-time
        # datetimes from datetime.fromtimestamp(). Reproduce that exactly.
        out = dict(raw)
        for key in ("at", "repliedAt"):
            if isinstance(out.get(key), str):
                out[key] = datetime.fromtimestamp(datetime.fromisoformat(out[key]).timestamp())
        return out


def adapter_for(case: Path) -> tuple[GooglePlayScraperAdapter, FakeLibrary]:
    lib = FakeLibrary(case)
    return GooglePlayScraperAdapter(app_fn=lib.app, reviews_fn=lib.reviews), lib


# --- GooglePlayScraperAdapter ---------------------------------------------

@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c.name)
def test_listing_matches_expected(case):
    adapter, _ = adapter_for(case)
    assert adapter.get_listing(load(case, "case.json")["package"]).to_dict() == load(case, "expected_listing.json")


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c.name)
def test_reviews_match_expected(case):
    adapter, _ = adapter_for(case)
    got = [r.to_dict() for r in adapter.get_reviews(load(case, "case.json")["package"])]
    assert got == load(case, "expected_reviews.json")


@pytest.mark.parametrize("case", ERROR_CASES, ids=lambda c: c.name)
def test_error_cases_raise_expected_reason(case):
    meta = load(case, "case.json")
    adapter, _ = adapter_for(case)
    with pytest.raises(SourceError) as e:
        adapter.get_listing(meta["package"])
    assert e.value.reason == meta["expected_reason"]
    assert adapter.exists(meta["package"]) is (meta["expected_reason"] != "not_found")


def test_reviews_stop_at_since_id():
    case = FIXTURES / "large"
    adapter, _ = adapter_for(case)
    got = adapter.get_reviews("com.example.chatlarge", since_id="r-large-2")
    assert [r.id for r in got] == ["r-large-3"]


def test_reviews_respect_limit_and_request_newest():
    case = FIXTURES / "large"
    adapter, lib = adapter_for(case)
    got = adapter.get_reviews("com.example.chatlarge", limit=2)
    assert [r.id for r in got] == ["r-large-3", "r-large-2"]
    first = next(c for c in lib.calls if c["fn"] == "reviews")
    assert first["sort"] == "NEWEST" and first["count"] == 2


def test_reviews_never_contain_reviewer_identity():
    adapter, _ = adapter_for(FIXTURES / "large")
    for r in adapter.get_reviews("com.example.chatlarge"):
        d = r.to_dict()
        assert "userName" not in d and "userImage" not in d
        assert not any("User" in str(v) for v in d.values())


def test_one_star_share():
    adapter, _ = adapter_for(FIXTURES / "large")
    listing = adapter.get_listing("com.example.chatlarge")
    assert listing.one_star_share == round(1803210 / sum(listing.rating_histogram), 4)
    adapter, _ = adapter_for(FIXTURES / "small")
    assert adapter.get_listing("io.example.tinytodo").one_star_share is None


@pytest.mark.parametrize(
    "exc, reason",
    [
        (NotFoundError("App not found(404)."), "not_found"),
        (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), "rate_limited"),
        (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "blocked"),
        (urllib.error.URLError("Tunnel connection failed: 403 Forbidden"), "blocked"),
        (urllib.error.URLError("timed out"), "unavailable"),
        (KeyError("ds:5"), "parse_failed"),
        (IndexError("list index out of range"), "parse_failed"),
        (RuntimeError("weird"), "unavailable"),
    ],
)
def test_library_errors_map_to_reasons(exc, reason):
    def boom(*a, **k):
        raise exc

    adapter = GooglePlayScraperAdapter(app_fn=boom, reviews_fn=boom)
    with pytest.raises(SourceError) as e:
        adapter.get_listing("com.example.app")
    assert e.value.reason == reason


def test_missing_title_is_parse_failed():
    adapter = GooglePlayScraperAdapter(app_fn=lambda *a, **k: {"appId": "com.example.app"}, reviews_fn=lambda *a, **k: ([], None))
    with pytest.raises(SourceError) as e:
        adapter.get_listing("com.example.app")
    assert e.value.reason == "parse_failed"


def test_schema_rejects_out_of_range_rating():
    data = load(FIXTURES / "large", "expected_listing.json") | {"rating": 7.0}
    with pytest.raises(SourceError) as e:
        validate_listing(Listing(**data))
    assert e.value.reason == "parse_failed" and "rating" in e.value.detail


def test_fallback_worthy_reasons():
    assert SourceError("blocked", "p").fallback_worthy
    assert SourceError("rate_limited", "p").fallback_worthy
    assert SourceError("parse_failed", "p").fallback_worthy
    assert not SourceError("not_found", "p").fallback_worthy
    with pytest.raises(ValueError):
        SourceError("nope", "p")  # type: ignore[arg-type]


# --- StubAdapter ------------------------------------------------------------

@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c.name)
def test_stub_returns_expected(case):
    pkg = load(case, "case.json")["package"]
    stub = StubAdapter()
    assert stub.get_listing(pkg).to_dict() == load(case, "expected_listing.json")
    assert [r.to_dict() for r in stub.get_reviews(pkg)] == load(case, "expected_reviews.json")
    assert stub.exists(pkg)


def test_stub_unknown_package_is_not_found():
    stub = StubAdapter()
    assert not stub.exists("com.example.gone")
    with pytest.raises(SourceError) as e:
        stub.get_listing("com.nobody.here")
    assert e.value.reason == "not_found"


# --- registry -----------------------------------------------------------------

def test_registry_switches_by_env(monkeypatch):
    monkeypatch.setenv("STORE_SOURCE", "stub")
    monkeypatch.delenv("STORE_SOURCE_FALLBACK", raising=False)
    assert registry.active_source().name == "stub"
    assert registry.fallback_source() is None


def test_registry_fallback(monkeypatch):
    monkeypatch.setenv("STORE_SOURCE", "google_play_scraper")
    monkeypatch.setenv("STORE_SOURCE_FALLBACK", "stub")
    assert registry.fallback_source().name == "stub"
    monkeypatch.setenv("STORE_SOURCE_FALLBACK", "google_play_scraper")
    assert registry.fallback_source() is None


def test_registry_unknown_adapter(monkeypatch):
    monkeypatch.setenv("STORE_SOURCE", "nope")
    with pytest.raises(ValueError, match="available"):
        registry.active_source()


@pytest.mark.parametrize("tz", ["UTC", "Asia/Kolkata", "America/Los_Angeles"])
def test_review_times_are_utc_on_any_machine_timezone(tz, monkeypatch):
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        adapter, _ = adapter_for(FIXTURES / "large")
        got = [r.created_at for r in adapter.get_reviews("com.example.chatlarge")]
        assert got == [r["created_at"] for r in load(FIXTURES / "large", "expected_reviews.json")]
    finally:
        monkeypatch.undo()
        time.tzset()

"""Record real google-play-scraper output for Contract Fixtures.

Runs in GitHub Actions (the Play Store is reachable there). Writes the raw
library output only: app.json and reviews_pages.json per case. Reviewer names
and avatars are stripped before writing. Expected files stay hand-reviewed.

Usage: python -m job.record_fixtures OUT_DIR large=com.whatsapp small=<pkg> removed=<pkg>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from job.sources.google_play import _default_fns

STRIP = ("userName", "userImage")


def _jsonable(review: dict) -> dict:
    out = {k: v for k, v in review.items() if k not in STRIP}
    for key in ("at", "repliedAt"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].astimezone(timezone.utc).isoformat()
    return out


def record(out_dir: Path, kind: str, package: str) -> None:
    app_fn, reviews_fn, newest = _default_fns()
    case = out_dir / kind
    case.mkdir(parents=True, exist_ok=True)
    meta = {"package": package, "kind": kind, "synthetic": False,
            "recorded_at": datetime.now(timezone.utc).isoformat()}
    try:
        raw = app_fn(package, lang="en", country="us")
    except Exception as e:  # noqa: BLE001 — record what the library raised
        meta["raises"] = type(e).__name__
        (case / "case.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(f"{kind}: {package} raised {type(e).__name__}")
        return
    keep = ("appId", "title", "developer", "icon", "version", "score", "ratings", "reviews",
            "histogram", "installs", "minInstalls", "realInstalls", "size", "updated", "recentChanges")
    (case / "app.json").write_text(json.dumps({k: raw.get(k) for k in keep}, indent=2, ensure_ascii=False) + "\n")
    page, _ = reviews_fn(package, lang="en", country="us", sort=newest, count=20)
    (case / "reviews_pages.json").write_text(
        json.dumps([[_jsonable(r) for r in page]], indent=2, ensure_ascii=False) + "\n")
    (case / "case.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"{kind}: {package} recorded ({len(page)} reviews)")


def record_app_store(out_dir: Path, kind: str, key: str) -> None:
    """Record Apple's raw responses for an App Key like ios:in:310633997 (reviewer names stripped)."""
    from job.sources.app_store import LOOKUP, MAX_PAGES, REVIEWS, _default_get
    from job.sources.base import parse_app_key

    k = parse_app_key(key)
    case = out_dir / "app_store" / kind
    case.mkdir(parents=True, exist_ok=True)
    meta = {"package": k.store_id, "country": k.country, "kind": kind, "synthetic": False,
            "recorded_at": datetime.now(timezone.utc).isoformat()}
    (case / "lookup.json").write_bytes(_default_get(LOOKUP.format(id=k.store_id, cc=k.country)))
    pages = []
    for page in range(1, min(MAX_PAGES, 2) + 1):
        url = REVIEWS.format(cc=k.country, page=page, id=k.store_id)
        try:
            raw = _default_get(url)
            data = json.loads(raw)
        except Exception as e:  # noqa: BLE001 — past the last page
            print(f"::notice title=app_store {kind} page {page}::{url} → {type(e).__name__}: {str(e)[:200]}")
            break
        feed = data.get("feed") or {}
        entry = feed.get("entry")
        n = len(entry) if isinstance(entry, list) else (1 if entry else 0)
        print(f"::notice title=app_store {kind} page {page}::{url} → {len(raw)} bytes, {n} entries, "
              f"feed keys {sorted(feed)[:8]}, start {raw[:160]!r}")
        entries = (data.get("feed") or {}).get("entry") or []
        for e in entries if isinstance(entries, list) else [entries]:
            e.pop("author", None)
        pages.append(data)
    (case / "reviews_pages.json").write_text(json.dumps(pages, indent=2, ensure_ascii=False) + "\n")
    (case / "case.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"app_store {kind}: {key} recorded ({len(pages)} pages)")


def probe(label: str, url: str) -> None:
    """Diagnostics: fetch a URL with our User-Agent and a browser one; report size and entry count."""
    import urllib.request

    agents = {"ours": "StoreRating/1.0 (+https://github.com/qureshiayaz29/storerating-bot)",
              "browser": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"}
    for name, ua in agents.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read()
            count = raw.count(b'"im:rating"') + raw.count(b"<im:rating")
            print(f"::notice title=probe {label} {name}::{url} → {len(raw)} bytes, {count} ratings, start {raw[:120]!r}")
        except Exception as e:  # noqa: BLE001
            print(f"::notice title=probe {label} {name}::{url} → {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    out = Path(sys.argv[1])
    for arg in sys.argv[2:]:
        kind, package = arg.split("=", 1)
        if package.startswith("url:"):
            probe(kind, package[4:])
        elif package.startswith("ios:"):
            record_app_store(out, kind, package)
        else:
            record(out, kind, package)

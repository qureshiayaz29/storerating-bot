"""Phase 4 — Fetch and Snapshots. See PLAN.md.

For every App with at least one Watcher: fetch the Listing and new Reviews
through the Active Source (Fallback Source on blocked/rate_limited/
parse_failed), write today's Snapshot and move the review cursor. Reviews stay
in memory for the Summary step and are never stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .db import JobRun, log_error, log_exception
from .sources.base import Listing, Review, SourceError, StoreSource

REVIEW_LIMIT = 500        # newest reviews read per App per run
FIRST_RUN_LIMIT = 200     # first run for an App: no cursor yet


@dataclass
class AppToFetch:
    package: str
    watchers: int
    last_review_id: str | None
    last_review_at: str | None


@dataclass
class FetchOutcome:
    package: str
    watchers: int
    listing: Listing | None = None
    reviews: list[Review] = field(default_factory=list)
    adapter: str | None = None
    removed: bool = False
    first_run: bool = False
    used_fallback: bool = False
    error: SourceError | None = None

    @property
    def ok(self) -> bool:
        return self.listing is not None


def apps_to_fetch(conn) -> list[AppToFetch]:
    rows = conn.execute(
        """select a.package, count(w.chat_id)::int as watchers,
                  c.last_review_id, c.last_review_at::text as last_review_at
           from apps a
           join watchlist w on w.package = a.package
           left join app_cursor c on c.package = a.package
           where not a.removed
           group by a.package, c.last_review_id, c.last_review_at
           order by watchers desc, a.package"""
    ).fetchall()
    return [AppToFetch(**r) for r in rows]


def fetch_app(app: AppToFetch, active: StoreSource, fallback: StoreSource | None, conn=None) -> FetchOutcome:
    out = FetchOutcome(app.package, app.watchers, first_run=app.last_review_id is None)
    source = active
    try:
        out.listing = active.get_listing(app.package)
    except SourceError as e:
        if e.reason == "not_found":
            out.removed, out.error = True, e
            return out
        if conn is not None:
            log_error(conn, "store_source", app.package, f"{active.name}: {e}")
        if not (fallback and e.fallback_worthy):
            out.error = e
            return out
        try:
            out.listing = fallback.get_listing(app.package)
            source, out.used_fallback = fallback, True
        except SourceError as e2:
            if conn is not None:
                log_error(conn, "store_source", app.package, f"{fallback.name}: {e2}")
            out.error = e2
            return out
    out.adapter = source.name

    try:
        limit = FIRST_RUN_LIMIT if out.first_run else REVIEW_LIMIT
        reviews = source.get_reviews(app.package, since_id=app.last_review_id, limit=limit)
        if app.last_review_at:  # the cursor review may have been deleted: also stop by time
            reviews = [r for r in reviews if r.created_at > _iso(app.last_review_at)]
        out.reviews = reviews
    except SourceError as e:
        if conn is not None:
            log_error(conn, "store_source", app.package, f"{source.name} reviews: {e}")
    return out


def save(conn, today: date, out: FetchOutcome) -> None:
    if out.removed:
        conn.execute("update apps set removed = true where package = %s", (out.package,))
        return
    if not out.ok:
        return
    l = out.listing
    conn.execute(
        """update apps set name = %s, developer = %s, icon_url = coalesce(%s, icon_url) where package = %s""",
        (l.name, l.developer, l.icon_url, out.package),
    )
    new_count = None if out.first_run else len(out.reviews)
    stars = [sum(1 for r in out.reviews if r.stars == s) for s in range(1, 6)] if out.reviews else None
    new_one_star = None
    if not out.first_run and out.reviews:
        new_one_star = round(sum(1 for r in out.reviews if r.stars == 1) / len(out.reviews), 4)
    conn.execute(
        """insert into app_snapshots (package, date, source_adapter, version, rating, review_count,
               installs_tier, size, one_star_share, updated_at, release_notes, new_reviews, new_one_star_share,
               new_star_counts)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           on conflict (package, date) do update set
               source_adapter = excluded.source_adapter, version = excluded.version, rating = excluded.rating,
               review_count = excluded.review_count, installs_tier = excluded.installs_tier, size = excluded.size,
               one_star_share = excluded.one_star_share, updated_at = excluded.updated_at,
               release_notes = excluded.release_notes,
               new_reviews = case when excluded.new_reviews is null then app_snapshots.new_reviews
                                  else coalesce(app_snapshots.new_reviews, 0) + excluded.new_reviews end,
               new_one_star_share = coalesce(excluded.new_one_star_share, app_snapshots.new_one_star_share),
               new_star_counts = case
                 when excluded.new_star_counts is null then app_snapshots.new_star_counts
                 when app_snapshots.new_star_counts is null then excluded.new_star_counts
                 else array(select a + b from unnest(app_snapshots.new_star_counts, excluded.new_star_counts) as t(a, b))
               end""",
        (out.package, today, out.adapter, l.version, l.rating, l.review_count, l.installs_tier, l.size,
         l.one_star_share, l.updated_at, l.release_notes, new_count, new_one_star, stars),
    )
    if out.reviews:
        newest = out.reviews[0]
        conn.execute(
            """insert into app_cursor (package, last_review_id, last_review_at) values (%s, %s, %s)
               on conflict (package) do update set last_review_id = excluded.last_review_id,
                                                   last_review_at = excluded.last_review_at""",
            (out.package, newest.id, newest.created_at),
        )


def run_fetch(conn, today: date, active: StoreSource, fallback: StoreSource | None, run: JobRun) -> list[FetchOutcome]:
    outcomes = []
    for app in apps_to_fetch(conn):
        try:
            out = fetch_app(app, active, fallback, conn)
            save(conn, today, out)
        except Exception as e:  # noqa: BLE001 — one bad App must not stop the run
            log_exception(conn, "store_source", app.package, e)
            out = FetchOutcome(app.package, app.watchers, error=SourceError("unavailable", app.package, str(e)))
        run.add("apps_fetched" if out.ok or out.removed else "apps_failed")
        run.add("reviews_processed", len(out.reviews))
        outcomes.append(out)
    return outcomes


def _iso(text: str) -> str:
    """Postgres timestamptz text → the same ISO form Reviews use (UTC, +00:00)."""
    from datetime import datetime, timezone

    return datetime.fromisoformat(text).astimezone(timezone.utc).isoformat()

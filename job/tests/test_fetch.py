from datetime import date

from job.db import JobRun
from job.fetch import FetchOutcome, apps_to_fetch, fetch_app, run_fetch
from job.health import check
from job.sources.base import Listing, Review, SourceError
from job.sources.stub import StubAdapter

TODAY = date(2026, 10, 29)
LARGE = "com.example.chatlarge"


def seed(conn, *packages, watchers=1):
    for i, p in enumerate(packages):
        conn.execute("insert into apps (package, name) values (%s, %s) on conflict do nothing", (p, p))
        for w in range(watchers):
            chat = 1000 * (i + 1) + w
            conn.execute("insert into users (chat_id) values (%s) on conflict do nothing", (chat,))
            conn.execute("insert into watchlist (chat_id, package) values (%s, %s)", (chat, p))


class Broken:
    name = "broken"

    def __init__(self, reason):
        self.reason = reason

    def get_listing(self, pkg):
        raise SourceError(self.reason, pkg, "test")

    def get_reviews(self, pkg, since_id=None, limit=500):
        raise SourceError(self.reason, pkg, "test")

    def exists(self, pkg):
        return False


def test_only_watched_apps_are_fetched_most_watched_first(conn):
    seed(conn, "io.example.tinytodo")
    seed(conn, LARGE, watchers=3)
    conn.execute("insert into apps (package, name) values ('nobody.watches.this', 'x')")
    assert [a.package for a in apps_to_fetch(conn)] == [LARGE, "io.example.tinytodo"]


def test_run_fetch_writes_snapshot_cursor_and_job_run(conn):
    seed(conn, LARGE, "io.example.tinytodo")
    run = JobRun(conn, "daily")
    outs = run_fetch(conn, TODAY, StubAdapter(), None, run)
    run.finish(True)
    snap = conn.execute("select * from app_snapshots where package = %s", (LARGE,)).fetchone()
    assert snap["source_adapter"] == "stub"
    assert float(snap["rating"]) == 4.19 and snap["review_count"] == 19873210
    assert float(snap["one_star_share"]) == round(1803210 / 19873210, 4)
    assert snap["new_reviews"] is None  # first run: no cursor, so no "new today" numbers
    cursor = conn.execute("select * from app_cursor where package = %s", (LARGE,)).fetchone()
    assert cursor["last_review_id"] == "r-large-3"
    jr = conn.execute("select * from job_runs").fetchone()
    assert (jr["apps_fetched"], jr["reviews_processed"], jr["success"]) == (2, 3, True)
    assert next(o for o in outs if o.package == LARGE).first_run


def test_second_run_counts_only_new_reviews(conn):
    seed(conn, LARGE)
    conn.execute("insert into app_cursor values (%s, 'r-large-2', '2026-10-28T07:00:00+00:00')", (LARGE,))
    run = JobRun(conn, "daily")
    outs = run_fetch(conn, TODAY, StubAdapter(), None, run)
    assert [r.id for r in outs[0].reviews] == ["r-large-3"]
    snap = conn.execute("select new_reviews, new_one_star_share from app_snapshots").fetchone()
    assert snap["new_reviews"] == 1 and float(snap["new_one_star_share"]) == 1.0


def test_removed_app_is_marked(conn):
    seed(conn, "com.example.gone")
    outs = run_fetch(conn, TODAY, StubAdapter(), None, JobRun(conn, "daily"))
    assert outs[0].removed
    assert conn.execute("select removed from apps where package = 'com.example.gone'").fetchone()["removed"]


def test_fallback_source_used_when_active_is_blocked(conn):
    seed(conn, LARGE)
    outs = run_fetch(conn, TODAY, Broken("blocked"), StubAdapter(), JobRun(conn, "daily"))
    assert outs[0].ok and outs[0].used_fallback and outs[0].adapter == "stub"
    err = conn.execute("select * from errors").fetchone()
    assert err["source"] == "store_source" and "broken" in err["message"]
    assert conn.execute("select source_adapter from app_snapshots").fetchone()["source_adapter"] == "stub"


def test_no_fallback_for_unavailable(conn):
    seed(conn, LARGE)
    run = JobRun(conn, "daily")
    outs = run_fetch(conn, TODAY, Broken("unavailable"), StubAdapter(), run)
    assert not outs[0].ok and outs[0].error.reason == "unavailable"
    assert run.counts["apps_failed"] == 1


def test_one_crashing_app_does_not_stop_the_run(conn):
    seed(conn, LARGE, "io.example.tinytodo")

    class Crashy(StubAdapter):
        def get_listing(self, pkg):
            if pkg == LARGE:
                raise RuntimeError("boom")
            return super().get_listing(pkg)

    run = JobRun(conn, "daily")
    outs = run_fetch(conn, TODAY, Crashy(), None, run)
    assert [o.ok for o in outs] == [False, True]
    assert conn.execute("select count(*) as n from errors").fetchone()["n"] == 1


def test_health_check():
    ok, msg = check(StubAdapter(), "com.example.chatlarge")
    assert ok, msg
    ok, msg = check(StubAdapter(), "io.example.tinytodo")
    assert not ok and "missing" in msg
    ok, msg = check(Broken("parse_failed"))
    assert not ok and "parse_failed" in msg

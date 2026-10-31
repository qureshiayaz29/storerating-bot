import json
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest

from job import summaries
from job.db import JobRun, set_config
from job.fetch import FetchOutcome
from job.sources.base import Review
from job.summaries import build_prompt, is_junk, parse, prepare, run_summaries, select

TODAY = date(2026, 10, 29)
REPLY = {
    "complaints": [{"theme": "Notes not syncing", "share": 41, "example": "Edits vanish after the update"}],
    "praise": [{"theme": "Faster search", "share": 22, "example": "Search is instant now"}],
    "verdict": "Sync bug is back after v5.3",
}


def rev(i: int, stars: int, text: str = "this is a real review text", thumbs: int = 0, hour: int = 0) -> Review:
    return Review(f"r{i}", stars, text, thumbs, None, datetime(2026, 10, 28, hour, tzinfo=timezone.utc).isoformat())


def outcome(pkg="com.example.app", n=10, watchers=1, first_run=False) -> FetchOutcome:
    listing = NS(name=f"App {pkg}")
    return FetchOutcome(pkg, watchers, listing=listing, first_run=first_run,  # type: ignore[arg-type]
                        reviews=[rev(i, 1 if i % 2 else 5, f"review number {i} about sync") for i in range(n)])


def message(reply=REPLY, tin=1000, tout=200):
    return NS(content=[NS(type="text", text=json.dumps(reply))], usage=NS(input_tokens=tin, output_tokens=tout))


class FakeClient:
    """Mimics anthropic.Anthropic().messages and .messages.batches."""

    def __init__(self, batch_status="ended", batch_items=None, create_fails=False):
        self.created, self.batch_requests = [], None
        self.status = batch_status
        self.items = batch_items
        self.create_fails = create_fails
        self.cancelled = False
        outer = self

        class Batches:
            def create(self, requests):
                if outer.create_fails:
                    raise RuntimeError("batches down")
                outer.batch_requests = list(requests)
                return NS(id="b1", processing_status="in_progress")

            def retrieve(self, _id):
                return NS(id="b1", processing_status=outer.status)

            def results(self, _id):
                if outer.items is not None:
                    return outer.items
                return [NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=message()))
                        for r in outer.batch_requests]

            def cancel(self, _id):
                outer.cancelled = True

        class Messages:
            batches = Batches()

            def create(self, **params):
                outer.created.append(params)
                return message()

        self.messages = Messages()


# --- selection and parsing ---------------------------------------------------------

def test_junk_reviews():
    assert is_junk("good") and is_junk("👍👍👍") and is_junk("10/10 !!!") and is_junk("nice app")
    assert not is_junk("crashes when I open settings")


def test_select_splits_dedupes_ranks_and_caps():
    reviews = [rev(1, 1, "sync is broken again", thumbs=2), rev(2, 2, "sync is  broken AGAIN", thumbs=50),
               rev(3, 5, "love the new search", thumbs=1), rev(4, 3, "it is okay i guess"),
               rev(5, 1, "login loop after update", thumbs=9, hour=5), rev(6, 1, "bad"),
               *[rev(100 + i, 2, f"complaint number {i} here") for i in range(200)]]
    bad, good = select(reviews)
    assert [r.id for r in good] == ["r3"]
    assert bad[0].id == "r5"  # 9 thumbs-up; r2 is a duplicate of r1's text and r1 came first
    assert "r2" not in {r.id for r in bad}
    assert len(bad) == summaries.MAX_PER_SIDE


def test_prompt_strips_nothing_personal_and_truncates():
    out = outcome()
    out.reviews.append(rev(99, 1, "x " * 2000))
    p = build_prompt(out)
    assert "App com.example.app" in p and "COMPLAINTS" in p
    assert max(len(line) for line in p.splitlines()) < summaries.MAX_REVIEW_CHARS + 40


def test_prepare_skips_first_run_and_few_reviews():
    assert prepare(outcome(n=4), batch=True) is None
    assert prepare(outcome(first_run=True), batch=True) is None
    assert prepare(outcome(n=10), batch=True).est_cost > 0


def test_parse_clamps_and_trims():
    s = parse("Sure! " + json.dumps({"complaints": [{"theme": "A", "share": 140, "example": "x"},
                                                    {"theme": "", "share": 5}] * 3,
                                     "praise": [], "verdict": "v" * 500}))
    assert s.complaints[0]["share"] == 100 and len(s.complaints) == 2 and len(s.verdict) == 120
    with pytest.raises(ValueError):
        parse("no json here")


# --- runs against the database -------------------------------------------------------

def seed_apps(conn, *pkgs):
    for p in pkgs:
        conn.execute("insert into apps (package, name) values (%s, %s)", (p, p))


def test_batch_run_stores_summaries_and_spend(conn):
    seed_apps(conn, "a.b", "c.d")
    client = FakeClient()
    run = JobRun(conn, "daily")
    res = run_summaries(conn, client, [outcome("a.b"), outcome("c.d", n=3)], TODAY, run,
                        deadline=1e18, sleep=lambda s: None)
    assert list(res.done) == ["a.b"] and res.skipped_few_reviews == ["c.d"]
    assert client.batch_requests[0]["params"]["model"] == "claude-haiku-5-5"
    row = conn.execute("select * from summaries").fetchone()
    assert row["verdict"] == "Sync bug is back after v5.3" and row["complaints"][0]["share"] == 41
    spend = conn.execute("select * from spend").fetchone()
    assert spend["batch"] and spend["tokens_in"] == 1000 and spend["source"] == "daily"
    assert run.counts["claude_calls"] == 1


def test_deadline_falls_back_to_standard_calls(conn):
    seed_apps(conn, "a.b")
    client = FakeClient(batch_status="in_progress")
    ticks = iter(range(100))
    res = run_summaries(conn, client, [outcome("a.b")], TODAY, JobRun(conn, "daily"),
                        deadline=3, sleep=lambda s: None, clock=lambda: next(ticks))
    assert client.cancelled and len(client.created) == 1 and "a.b" in res.done
    assert conn.execute("select batch from spend").fetchone()["batch"] is False


def test_failed_batch_item_is_retried_standard(conn):
    seed_apps(conn, "a.b")
    client = FakeClient(batch_items=[NS(custom_id="r0", result=NS(type="errored"))])
    res = run_summaries(conn, client, [outcome("a.b")], TODAY, JobRun(conn, "daily"), deadline=1e18,
                        sleep=lambda s: None)
    assert "a.b" in res.done and len(client.created) == 1


def test_batch_create_failure_falls_back(conn):
    seed_apps(conn, "a.b")
    client = FakeClient(create_fails=True)
    res = run_summaries(conn, client, [outcome("a.b")], TODAY, JobRun(conn, "daily"), deadline=1e18)
    assert "a.b" in res.done
    assert conn.execute("select count(*) as n from errors where source = 'claude'").fetchone()["n"] == 1


def test_priority_order_under_a_tight_budget(conn):
    seed_apps(conn, "big.app", "small.app")
    big = outcome("big.app", watchers=50)
    small = outcome("small.app", watchers=1)
    est = prepare(big, batch=True).est_cost
    days = 31
    set_config(conn, "monthly_budget_usd", str(est * 1.3 / 0.8 * days))  # room for one request today
    res = run_summaries(conn, FakeClient(), [small, big], TODAY, JobRun(conn, "daily"), deadline=1e18,
                        sleep=lambda s: None, now=datetime(2026, 10, 1, 6, tzinfo=timezone.utc))
    assert list(res.done) == ["big.app"] and res.skipped_budget == ["small.app"]


def test_kill_switch_makes_no_calls(conn, monkeypatch):
    monkeypatch.setenv("CLAUDE_ENABLED", "false")
    seed_apps(conn, "a.b")
    client = FakeClient()
    res = run_summaries(conn, client, [outcome("a.b")], TODAY, JobRun(conn, "daily"), deadline=1e18)
    assert res.done == {} and res.skipped_budget == ["a.b"] and client.batch_requests is None


def test_unparseable_reply_is_logged(conn, monkeypatch):
    seed_apps(conn, "a.b")
    client = FakeClient(batch_items=[NS(custom_id="r0", result=NS(type="succeeded",
                                                                     message=NS(content=[NS(text="oops")],
                                                                                usage=NS(input_tokens=1, output_tokens=1))))])
    res = run_summaries(conn, client, [outcome("a.b")], TODAY, JobRun(conn, "daily"), deadline=1e18,
                        sleep=lambda s: None)
    assert res.failed == ["a.b"] and res.done == {}
    assert conn.execute("select count(*) as n from spend").fetchone()["n"] == 1  # still paid for


def test_params_disable_thinking_and_leave_headroom():
    from job.summaries import MAX_TOKENS, _params
    p = _params("x")
    assert p["thinking"] == {"type": "disabled"} and p["max_tokens"] == MAX_TOKENS >= 1000


def test_parse_sorts_by_share_and_keeps_since():
    from job.summaries import parse
    s = parse('{"complaints": [{"theme": "a", "share": 12}, {"theme": "b", "share": 25, "status": "persisting", '
              '"since": "2026-10-28"}, {"theme": "c"}], "praise": [], "verdict": "v"}')
    assert [c["theme"] for c in s.complaints] == ["b", "a", "c"]
    assert s.complaints[0]["since"] == "2026-10-28" and "since" not in s.complaints[1]


def test_daily_context_lists_recent_themes(conn):
    from datetime import date
    from job.db import Jsonb
    from job.summaries import daily_context
    conn.execute("insert into apps (package) values ('p')")
    assert daily_context(conn, "p", date(2026, 10, 31)) == ""
    conn.execute("""insert into summaries (package, period, period_start, complaints, praise, verdict)
                    values ('p', 'daily', '2026-10-30', %s, '[]', 'v')""", (Jsonb([{"theme": "Charts slow"}]),))
    ctx = daily_context(conn, "p", date(2026, 10, 31))
    assert "Today: 2026-10-31" in ctx and "2026-10-30: Charts slow" in ctx and '"since"' in ctx

from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from job import reports
from job.db import JobRun, Jsonb
from job.reports import (Period, eligible_apps, periods_due, previous_month, previous_quarter, report_card,
                         run_reports, source_lines, themes_for)
from job.summaries import parse
from job.telegram import Telegram
from job.tests.test_on_demand import FakeRenderer
from job.tests.test_send import seed as seed_user, card
from job.tests.test_summaries import FakeClient, message

OCT = Period("monthly", date(2026, 10, 1), date(2026, 10, 31))
Q3 = Period("quarterly", date(2026, 7, 1), date(2026, 9, 30))
REPLY = {"complaints": [{"theme": "Sync issues", "share": 40, "status": "persisting", "example": "x"},
                        {"theme": "Ads", "share": 20, "status": "fixed", "example": "y"},
                        {"theme": "Battery drain", "share": 10, "status": "new", "example": "z"}],
         "praise": [{"theme": "Fast search", "share": 30, "example": "q"}],
         "verdict": "Rough month: sync broke, ads fixed"}


def test_periods():
    assert periods_due(date(2026, 10, 2)) == []
    assert [p.kind for p in periods_due(date(2026, 11, 1))] == ["monthly"]
    due = periods_due(date(2026, 10, 1))
    assert [(p.kind, p.start, p.end) for p in due] == [("monthly", date(2026, 9, 1), date(2026, 9, 30)),
                                                       ("quarterly", date(2026, 7, 1), date(2026, 9, 30))]
    assert previous_month(date(2026, 1, 1)) == Period("monthly", date(2025, 12, 1), date(2025, 12, 31))
    assert previous_quarter(date(2026, 1, 1)) == Period("quarterly", date(2025, 10, 1), date(2025, 12, 31))
    assert OCT.label == "October 2026" and Q3.label == "Q3 · Jul–Sep 2026"


def test_parse_keeps_status():
    s = parse(__import__("json").dumps(REPLY | {"complaints": REPLY["complaints"] + [{"theme": "t", "status": "odd"}]}))
    assert [c.get("status") for c in s.complaints] == ["persisting", "fixed", "new"]


def snapshots(conn, pkg, start: date, days: int, rating=lambda i: 4.3 - i * 0.005, reviews=10):
    for i in range(days):
        conn.execute("""insert into app_snapshots (package, date, source_adapter, rating, version, new_reviews)
                        values (%s, %s, 'stub', %s, '5.3', %s)""", (pkg, start + timedelta(days=i), rating(i), reviews))


_chat = iter(range(100, 10_000))


def app(conn, pkg="a.app", watched=True):
    conn.execute("insert into apps (package, name) values (%s, 'Notely') on conflict do nothing", (pkg,))
    if watched:
        chat = next(_chat)
        seed_user(conn, chat, packages=())
        conn.execute("insert into watchlist (chat_id, package) values (%s, %s)", (chat, pkg))


def test_eligible_apps_need_coverage_from_early_in_the_period(conn):
    app(conn, "full.app")
    snapshots(conn, "full.app", OCT.start, 31)
    app(conn, "late.app")
    snapshots(conn, "late.app", date(2026, 10, 20), 12)
    app(conn, "nowatch.app", watched=False)
    snapshots(conn, "nowatch.app", OCT.start, 31)
    assert eligible_apps(conn, OCT) == ["full.app"]


def test_monthly_card(conn):
    app(conn)
    snapshots(conn, "a.app", OCT.start, 31)
    conn.execute("""insert into alerts (package, type, dedupe_key, detail, detected_at)
                    values ('a.app', 'new_version', 'v:5.3', %s, '2026-10-14')""", (Jsonb({"to": "5.3"}),))
    d, complaints, praise = report_card(conn, "a.app", OCT, parse(__import__("json").dumps(REPLY)))
    assert d.period == "Monthly" and d.period_label == "October 2026"
    assert len(d.points) == 31 and d.points[0] == pytest.approx(4.3)
    assert d.rating == pytest.approx(4.15) and d.delta == pytest.approx(-0.15)
    assert d.releases == [(13, "5.3")]
    assert ("310", "reviews") in d.stats and ("1", "releases") in d.stats and ("4.15", "lowest") in d.stats
    assert d.chips[:2] == [("bad", "Sync issues (persisting)"), ("bad", "Ads (fixed)")]
    assert d.takeaway == "Rough month: sync broke, ads fixed" and d.verdict.startswith("Rating down")


def test_quarterly_card_uses_weekly_averages(conn):
    app(conn)
    snapshots(conn, "a.app", Q3.start, 92)
    d, _, _ = report_card(conn, "a.app", Q3, None)
    assert d.period == "Quarterly" and len(d.points) == 14 and d.chart_title == "14 weeks"
    week1 = conn.execute("select round(avg(rating), 3)::float as a from app_snapshots where date < %s",
                         (Q3.start + timedelta(days=7),)).fetchone()["a"]
    assert d.points[0] == pytest.approx(week1)
    assert d.verdict == "Rating down 4.3 → 3.9."
    assert [lab for _, lab in d.x_labels] == ["Jul", "Aug", "Sep", "End"]


def test_themes_need_at_least_two_summaries_and_record_spend(conn):
    app(conn)
    run = JobRun(conn, "reports")
    client = FakeClient()
    assert themes_for(conn, client, run, "a.app", "Notely", OCT) is None
    for day in (2, 20):
        conn.execute("""insert into summaries (package, period, period_start, complaints, praise, verdict)
                        values ('a.app', 'daily', %s, %s, '[]', 'v')""",
                     (date(2026, 10, day), Jsonb([{"theme": "Sync", "share": 50}])))
    assert source_lines(conn, "a.app", OCT)[0] == "2026-10-02 | Sync ~50% | - | v"
    s = themes_for(conn, client, run, "a.app", "Notely", OCT)
    assert s is not None and client.created[0]["messages"][0]["content"].startswith("App: Notely")
    assert conn.execute("select source, batch from spend").fetchone() == {"source": "report", "batch": False}
    assert conn.execute("select period from summaries where period = 'monthly'").fetchone()


def test_run_reports_uploads_cards_and_deliver_picks_them_up(conn, monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")
    seed_user(conn, 7, digest="00:00", packages=("a.app",))
    snapshots(conn, "a.app", date(2026, 7, 1), 92)
    tg = Telegram(token=None)
    made = run_reports(conn, tg, None, JobRun(conn, "reports"), date(2026, 10, 1), renderer_factory=FakeRenderer)
    assert made == {"monthly": 1, "quarterly": 1}
    periods = {r["period"] for r in conn.execute("select period from cards").fetchall()}
    assert periods == {"monthly", "quarterly"}
    from job.send import due_updates
    from datetime import datetime, timezone
    items = due_updates(conn, datetime.now(timezone.utc))
    assert [(i.kind, i.silent) for i in items] == [("monthly", False), ("quarterly", True)]


def test_nothing_runs_mid_month(conn):
    assert run_reports(conn, Telegram(token=None), None, JobRun(conn, "reports"), date(2026, 10, 15)) == {}

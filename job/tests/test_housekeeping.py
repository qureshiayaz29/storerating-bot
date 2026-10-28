from datetime import date, datetime, timedelta, timezone

from job import cleanup, platform_usage as platform, stats
from job.db import Jsonb, set_config
from job.telegram import Telegram

DAY = date(2026, 10, 29)
MIDDAY = datetime(2026, 10, 29, 6, 30, tzinfo=timezone.utc)  # 12:00 IST


def event(conn, chat, name, ts=MIDDAY, props=None):
    conn.execute("insert into events (chat_id, event, props, ts) values (%s, %s, %s, %s)",
                 (chat, name, Jsonb(props or {}), ts))


def test_daily_stats(conn):
    conn.execute("""insert into users (chat_id, created_at, blocked) values
                    (1, %s, false), (2, %s, false), (3, %s, true)""",
                 (MIDDAY - timedelta(days=10), MIDDAY, MIDDAY - timedelta(days=3)))
    conn.execute("insert into apps (package) values ('a'), ('b')")
    conn.execute("insert into watchlist (chat_id, package) values (1, 'a'), (2, 'a'), (2, 'b')")
    event(conn, 1, "view_apps")
    event(conn, 2, "add_app")
    event(conn, 1, "update_sent")
    event(conn, 2, "update_sent")
    event(conn, 1, "alert_sent")
    event(conn, 3, "bot_blocked")
    event(conn, 1, "view_apps", ts=MIDDAY - timedelta(days=1))  # yesterday: not counted
    conn.execute("insert into spend (ts, source, model, cost_usd) values (%s, 'daily', 'm', 0.42)", (MIDDAY,))
    row = stats.build(conn, DAY)
    assert row["users_total"] == 3 and row["users_new"] == 1
    assert row["dau_active"] == 2 and row["dau_reached"] == 2
    assert row["blocked_total"] == 1 and row["blocked_new"] == 1
    assert row["apps_watched"] == 2 and row["updates_sent"] == 2 and row["alerts_sent"] == 1
    assert float(row["claude_cost_usd"]) == 0.42
    assert conn.execute("select count(*) as n from daily_stats").fetchone()["n"] == 1
    stats.build(conn, DAY)  # idempotent
    assert conn.execute("select count(*) as n from daily_stats").fetchone()["n"] == 1


def test_retention(conn):
    week_ago = MIDDAY - timedelta(days=7)
    conn.execute("""insert into users (chat_id, created_at, blocked, paused) values
                    (1, %s, false, false), (2, %s, true, false), (3, %s, false, true), (4, %s, false, false)""",
                 (week_ago, week_ago, week_ago, week_ago))
    assert stats.retention(conn, DAY, 7) == 0.5
    assert stats.retention(conn, DAY, 1) is None


def test_platform_usage_and_alerts(conn, monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")
    set_config(conn, "vercel_limit_invocations", "1000")
    conn.execute("""insert into fn_metrics (date, route, invocations, duration_ms_total, response_bytes_total)
                    values ('2026-10-02', '/api/telegram', 500, 3600000, 2000000000),
                           ('2026-10-28', '/api/telegram', 220, 0, 0),
                           ('2026-09-30', '/api/telegram', 999, 0, 0)""")
    tg = Telegram(token=None)
    usage = {u.resource: u for u in platform.run(conn, DAY, tg)}
    assert usage["invocations"].used == 720 and round(usage["invocations"].pct) == 72
    assert usage["active_cpu"].used == 1.0 and usage["data_transfer"].used == 2.0
    assert usage["db_size"].used > 0
    assert conn.execute("select count(*) as n from platform_usage where provider = 'vercel'").fetchone()["n"] == 3
    texts = [m["text"] for m in tg.sent]
    assert len(texts) == 1 and "Vercel usage at 70%" in texts[0] and "720 of 1,000 invocations" in texts[0]
    assert platform.check_alerts(conn, DAY, list(usage.values()), tg) == []  # once per month
    conn.execute("update fn_metrics set invocations = 700 where date = '2026-10-28'")
    sent = platform.check_alerts(conn, DAY, platform.collect(conn, DAY), tg)
    assert sent == ["platform_alert:2026-10:vercel:invocations:90"]


def test_cleanup(conn):
    conn.execute("insert into apps (package) values ('a')")
    old = datetime.now(timezone.utc) - timedelta(days=100)
    event(conn, 1, "view_apps", ts=old)
    event(conn, 1, "view_apps")
    conn.execute("insert into errors (source, message, ts) values ('web', 'x', %s)", (old,))
    today = date.today()
    for i in range(95, 110):  # two weeks of old daily snapshots
        conn.execute("insert into app_snapshots (package, date, source_adapter) values ('a', %s, 'stub')",
                     (today - timedelta(days=i),))
    conn.execute("insert into app_snapshots (package, date, source_adapter) values ('a', %s, 'stub')", (today,))
    out = cleanup.run(conn, today)
    assert out["events"] == 1 and out["errors"] == 1
    left = conn.execute("select date from app_snapshots order by date").fetchall()
    weeks = {(r["date"].isocalendar().year, r["date"].isocalendar().week) for r in left[:-1]}
    assert len(left) - 1 == len(weeks)  # one per old week, plus today's

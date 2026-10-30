"""Full run with the stub Store Source: daily job then delivery, no network."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from job import daily, deliver
from job.tests.test_card import _chromium_available


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
def test_daily_then_deliver(conn, db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORE_SOURCE", "stub")
    monkeypatch.delenv("STORE_SOURCE_FALLBACK", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")
    conn.execute("insert into users (chat_id, digest_time) values (1, '00:00'), (2, '00:00'), (3, '23:59')")
    conn.execute("insert into apps (package, name) values ('com.example.chatlarge', 'x'), ('com.example.gone', 'Gone')")
    conn.execute("""insert into watchlist (chat_id, package) values (1, 'com.example.chatlarge'),
                    (2, 'com.example.chatlarge'), (3, 'com.example.chatlarge'), (1, 'com.example.gone')""")

    daily.main()
    out = capsys.readouterr().out
    assert "Summaries skipped: ANTHROPIC_API_KEY not set" in out
    snap = conn.execute("select * from app_snapshots").fetchone()
    assert snap["package"] == "com.example.chatlarge"
    assert conn.execute("select removed from apps where package = 'com.example.gone'").fetchone()["removed"]
    card = conn.execute("select * from cards").fetchone()
    assert card["telegram_file_id"].startswith("dry-") and "ChatLarge Messenger" in card["caption"]
    alert = conn.execute("select type, sent_at from alerts").fetchone()
    assert alert["type"] == "app_removed" and alert["sent_at"] is not None
    run = conn.execute("select * from job_runs where workflow = 'daily'").fetchone()
    assert run["success"] and run["apps_fetched"] == 2 and run["cards_rendered"] == 1 and run["sent"] == 1

    deliver.main()
    sent = conn.execute("select chat_id from deliveries where kind = 'daily' order by chat_id").fetchall()
    if datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M") < "23:59":
        assert [r["chat_id"] for r in sent] == [1, 2]   # user 3's digest time has not arrived
    deliver.main()
    assert conn.execute("select count(*) as n from deliveries where kind = 'daily'").fetchone()["n"] == len(sent)


def test_daily_job_runs_once_per_day(conn):
    from datetime import date
    from job.daily import already_ran
    today = date(2026, 10, 30)
    assert not already_ran(conn, today)
    conn.execute("""insert into job_runs (workflow, started_at, finished_at, success)
                    values ('daily', '2026-10-29 23:35+00', '2026-10-29 23:50+00', true)""")  # 05:05 IST on the 30th
    assert already_ran(conn, today) and not already_ran(conn, date(2026, 10, 31))

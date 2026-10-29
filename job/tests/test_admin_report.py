from datetime import date, timedelta

import pytest

from job import admin_report
from job.admin_report import caption_text, card_html, collect, is_due, send
from job.telegram import Telegram
from job.tests.test_card import _chromium_available
from job.tests.test_on_demand import FakeRenderer

MONDAY = date(2026, 11, 2)


def seed_stats(conn):
    for i in range(14):
        d = MONDAY - timedelta(days=14 - i)
        cur = i >= 7
        conn.execute(
            """insert into daily_stats (date, users_new, dau_active, dau_reached, blocked_new, updates_sent, alerts_sent,
                                        claude_cost_usd, d7_retention) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (d, 4 if cur else 2, 30, 60 if cur else 40, 1, 100, 3, 0.5 if cur else 0.25, 0.7 if cur else None))
    conn.execute("insert into users (chat_id) select g from generate_series(1, 25) g")
    conn.execute("insert into apps (package, name) values ('a', 'Alpha'), ('b', 'Beta')")
    conn.execute("insert into watchlist (chat_id, package) select g, case when g % 3 = 0 then 'b' else 'a' end "
                 "from generate_series(1, 25) g")
    conn.execute("""insert into platform_usage (date, provider, resource, used, "limit", unit, source)
                    values (current_date, 'vercel', 'invocations', 750000, 1000000, 'invocations', 'self_metered')""")


def test_due_on_mondays_only():
    assert is_due(MONDAY) and not is_due(MONDAY + timedelta(days=1))


def test_collect_compares_this_week_with_last(conn):
    seed_stats(conn)
    w = collect(conn, MONDAY)
    assert (w.start, w.end) == (date(2026, 10, 26), date(2026, 11, 1))
    assert w.users_total == 25 and w.new_users == 28 and w.new_prev == 14
    assert w.reached_avg == 60 and w.reached_prev == 40 and w.reached_daily == [60] * 7
    assert w.spend == pytest.approx(3.5) and w.d7 == pytest.approx(0.7)
    assert w.top_apps == [("Alpha", 17), ("Beta", 8)]
    assert ("vercel invocations", 75.0) in w.limits


def test_caption(conn):
    seed_stats(conn)
    text = caption_text(collect(conn, MONDAY))
    assert "<b>StoreRating weekly</b> · 26 Oct – 01 Nov" in text
    assert "60 reached/day (▲ 50%)" in text and "D7 retention 70%" in text
    assert "Claude $3.50 (▲ 100%)" in text and "Near limits: vercel invocations 75%" in text
    assert len(text) <= 1024


def test_card_escapes_app_names(conn):
    seed_stats(conn)
    conn.execute("update apps set name = '<script>x</script>' where package = 'a'")
    assert "<script>x" not in card_html(collect(conn, MONDAY))


def test_send_uploads_to_admin(conn, monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")
    seed_stats(conn)
    tg = Telegram(token=None)
    assert send(conn, tg, MONDAY, renderer_factory=FakeRenderer)
    assert tg.sent[0]["method"] == "sendPhoto" and tg.sent[0]["chat_id"] == 999


def test_send_without_admin_is_a_no_op(conn, monkeypatch):
    monkeypatch.delenv("ADMIN_CHAT_ID", raising=False)
    assert not send(conn, Telegram(token=None), MONDAY, renderer_factory=FakeRenderer)


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
def test_renders(conn):
    seed_stats(conn)
    from job.card.render import Renderer
    with Renderer() as r:
        png = r.render(card_html(collect(conn, MONDAY)))
    assert png[:4] == b"\x89PNG"
    open("/tmp/claude-0/-home-claude/c2577740-00c2-5ca4-b969-fd7c2416cd07/scratchpad/weekly.png", "wb").write(png)

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from job.db import JobRun
from job.on_demand import pending, run_on_demand
from job.telegram import Telegram
from job.tests.test_summaries import FakeClient

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)
LARGE = "com.example.chatlarge"


class FakeRenderer:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def render(self, html):
        return b"\x89PNG fake"


@pytest.fixture(autouse=True)
def stub_source(monkeypatch):
    monkeypatch.setenv("STORE_SOURCE", "stub")
    monkeypatch.delenv("STORE_SOURCE_FALLBACK", raising=False)
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")


def seed(conn, chats, pkg=LARGE, paused_chat=None):
    conn.execute("insert into apps (package, name) values (%s, 'x') on conflict do nothing", (pkg,))
    for c in chats:
        conn.execute("insert into users (chat_id, paused) values (%s, %s) on conflict do nothing", (c, c == paused_chat))
        conn.execute("insert into watchlist (chat_id, package) values (%s, %s)", (c, pkg))
        conn.execute("insert into on_demand (chat_id, package) values (%s, %s)", (c, pkg))


def test_pending_groups_by_app(conn):
    seed(conn, [1, 2])
    assert pending(conn) == {LARGE: [1, 2]}


def test_run_fetches_renders_uploads_and_sends(conn):
    seed(conn, [1, 2, 3], paused_chat=3)
    tg = Telegram(token=None)
    run = JobRun(conn, "on_demand")
    res = run_on_demand(conn, tg, None, run, renderer_factory=FakeRenderer, now=NOW)
    assert res == {LARGE: "done"}
    methods = [m["method"] for m in tg.sent]
    assert methods.count("sendPhoto") == 3  # one upload + users 1 and 2; paused user 3 skipped
    card = conn.execute("select * from cards").fetchone()
    assert card["telegram_file_id"].startswith("dry-") and "ChatLarge" in card["caption"]
    statuses = {r["status"] for r in conn.execute("select status from on_demand").fetchall()}
    assert statuses == {"done"}
    sent = conn.execute("select chat_id from deliveries order by chat_id").fetchall()
    assert [r["chat_id"] for r in sent] == [1, 2]


def test_existing_fresh_card_is_reused_without_fetching(conn):
    seed(conn, [1])
    conn.execute("""insert into cards (package, period, period_start, telegram_file_id, caption, created_at)
                    values (%s, 'daily', '2026-10-29', 'F-old', 'cap', %s)""", (LARGE, NOW - timedelta(hours=2)))
    tg = Telegram(token=None)
    run_on_demand(conn, tg, None, JobRun(conn, "on_demand"), renderer_factory=FakeRenderer, now=NOW)
    assert [m["photo"] for m in tg.sent if m["method"] == "sendPhoto"] == ["F-old"]
    assert conn.execute("select count(*) as n from app_snapshots").fetchone()["n"] == 0


def test_first_reviews_are_summarised_with_a_standard_call(conn, monkeypatch):
    seed(conn, [1])
    import job.fetch as f
    monkeypatch.setattr(f, "FIRST_RUN_LIMIT", 500)
    import job.summaries as s
    monkeypatch.setattr(s, "MIN_NEW_REVIEWS", 1)
    client = FakeClient()
    run_on_demand(conn, Telegram(token=None), client, JobRun(conn, "on_demand"), renderer_factory=FakeRenderer, now=NOW)
    assert len(client.created) == 1 and client.batch_requests is None
    assert conn.execute("select source, batch from spend").fetchone() == {"source": "on_demand", "batch": False}
    assert conn.execute("select verdict from summaries").fetchone()["verdict"] == "Sync bug is back after v5.3"


def test_failed_app_is_marked_failed(conn):
    seed(conn, [1], pkg="com.example.gone")
    res = run_on_demand(conn, Telegram(token=None), None, JobRun(conn, "on_demand"), renderer_factory=FakeRenderer,
                        now=NOW)
    assert res == {"com.example.gone": "failed"}
    assert conn.execute("select status from on_demand").fetchone()["status"] == "failed"

from datetime import date, datetime, timedelta, timezone

import pytest

from job.db import Jsonb
from job.send import (GLOBAL_PER_SECOND, Outgoing, Pacer, alert_text, due_updates, pending_alerts,
                      silence_after_first, upload_card)
from job.telegram import Telegram, TelegramSendError

NOW = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)  # 11:30 in India, 06:00 in London


class FakeTg(Telegram):
    def __init__(self, fail: dict[int, list[TelegramSendError]] | None = None):
        super().__init__(token=None)
        self.fail = fail or {}
        self.calls: list[tuple[int, str, bool]] = []

    def call(self, method, payload):
        chat = payload.get("chat_id")
        errs = self.fail.get(chat)
        if errs:
            raise errs.pop(0)
        self.calls.append((chat, method, payload.get("disable_notification", False)))
        return {"ok": True, "result": {}}


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def now(self):
        return self.t


def seed(conn, chat, tz="Asia/Kolkata", digest="11:00", packages=("a.app",), paused=False, blocked=False):
    conn.execute("insert into users (chat_id, timezone, digest_time, paused, blocked) values (%s, %s, %s, %s, %s)",
                 (chat, tz, digest, paused, blocked))
    for p in packages:
        conn.execute("insert into apps (package, name) values (%s, %s) on conflict do nothing", (p, p.upper()))
        conn.execute("insert into watchlist (chat_id, package) values (%s, %s)", (chat, p))


def card(conn, package="a.app", day=date(2026, 10, 29), created=NOW - timedelta(hours=1)):
    conn.execute("""insert into cards (package, period, period_start, telegram_file_id, caption, created_at)
                    values (%s, 'daily', %s, %s, 'cap', %s)""", (package, day, f"file-{package}", created))


# --- Pacer ---------------------------------------------------------------------------

def test_pacer_round_robin_and_rate_limits(conn):
    for c in range(30):
        conn.execute("insert into apps (package) values (%s) on conflict do nothing", ("a.app",))
    items = [Outgoing(chat, "a.app", "daily", "2026-10-29", "cap", file_id="f") for chat in range(30) for _ in range(2)]
    tg, clock = FakeTg(), Clock()
    stats = Pacer(conn, tg, sleep=clock.sleep, clock=clock.now).send_all(items)
    assert stats["sent"] == 60
    # First second: 25 different chats, never the same chat twice in one round.
    first_round = [c for c, _, _ in tg.calls[:GLOBAL_PER_SECOND]]
    assert len(set(first_round)) == GLOBAL_PER_SECOND
    assert len(clock.sleeps) >= 2


def test_pacer_retries_429_then_succeeds(conn):
    conn.execute("insert into apps (package) values ('a.app')")
    tg = FakeTg({1: [TelegramSendError("sendPhoto", 429, "Too Many Requests", retry_after=3)]})
    clock = Clock()
    stats = Pacer(conn, tg, sleep=clock.sleep, clock=clock.now).send_all(
        [Outgoing(1, "a.app", "daily", "d", "c", file_id="f")])
    assert stats == {"sent": 1, "failed": 0, "blocked": 0, "rate_limited": 1}
    assert 3 in clock.sleeps


def test_pacer_marks_blocked_users_and_skips_their_queue(conn):
    seed(conn, 7, packages=("a.app", "b.app"))
    tg = FakeTg({7: [TelegramSendError("sendPhoto", 403, "Forbidden: bot was blocked by the user")]})
    stats = Pacer(conn, tg, sleep=lambda s: None).send_all(
        [Outgoing(7, "a.app", "daily", "d", "c", file_id="f"), Outgoing(7, "b.app", "daily", "d", "c", file_id="f")])
    assert stats["blocked"] == 1 and stats["sent"] == 0 and tg.calls == []
    assert conn.execute("select blocked from users where chat_id = 7").fetchone()["blocked"]


def test_pacer_logs_other_failures(conn):
    conn.execute("insert into apps (package) values ('a.app')")
    tg = FakeTg({1: [TelegramSendError("sendPhoto", 400, "Bad Request: wrong file id")]})
    stats = Pacer(conn, tg, sleep=lambda s: None).send_all([Outgoing(1, "a.app", "daily", "d", "c", file_id="f")])
    assert stats["failed"] == 1
    assert conn.execute("select count(*) as n from errors where source = 'telegram'").fetchone()["n"] == 1


def test_silent_after_first():
    items = silence_after_first([Outgoing(1, "a", "daily", "d", ""), Outgoing(1, "b", "daily", "d", ""),
                                 Outgoing(2, "a", "daily", "d", "")])
    assert [i.silent for i in items] == [False, True, False]


# --- due updates -----------------------------------------------------------------------

def test_due_respects_digest_time_and_timezone(conn):
    seed(conn, 1, tz="Asia/Kolkata", digest="11:00")      # 11:30 local → due
    seed(conn, 2, tz="Europe/London", digest="11:00")     # 06:00 local → not yet
    seed(conn, 3, tz="Asia/Kolkata", digest="12:00")      # 11:30 local → not yet
    card(conn)
    assert [i.chat_id for i in due_updates(conn, NOW)] == [1]


def test_due_skips_paused_blocked_and_app_paused(conn):
    seed(conn, 1, paused=True)
    seed(conn, 2, blocked=True)
    seed(conn, 3)
    conn.execute("update watchlist set paused = true where chat_id = 3")
    seed(conn, 4)
    card(conn)
    assert [i.chat_id for i in due_updates(conn, NOW)] == [4]


def test_due_never_resends_and_ignores_stale_cards(conn):
    seed(conn, 1, packages=("a.app", "b.app"))
    card(conn, "a.app")
    card(conn, "b.app", day=date(2026, 10, 27), created=NOW - timedelta(days=2))
    items = due_updates(conn, NOW)
    assert [(i.package, i.file_id, i.silent) for i in items] == [("a.app", "file-a.app", False)]
    Pacer(conn, FakeTg(), sleep=lambda s: None).send_all(items)
    assert due_updates(conn, NOW) == []
    assert conn.execute("select count(*) as n from events where event = 'update_sent'").fetchone()["n"] == 1


def test_identical_update_for_all_watchers(conn):
    seed(conn, 1)
    seed(conn, 2)
    card(conn)
    items = due_updates(conn, NOW)
    assert {i.file_id for i in items} == {"file-a.app"} and len(items) == 2
    assert conn.execute("select count(*) as n from cards").fetchone()["n"] == 1


def test_upload_card_stores_file_id_and_cleans_up(conn, monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")
    conn.execute("insert into apps (package) values ('a.app')")
    tg = Telegram(token=None)
    fid = upload_card(conn, tg, "a.app", "daily", date(2026, 10, 29), b"png", "cap")
    row = conn.execute("select telegram_file_id, caption from cards").fetchone()
    assert row == {"telegram_file_id": fid, "caption": "cap"}
    assert [m["method"] for m in tg.sent] == ["sendPhoto", "deleteMessage"]


# --- alerts ---------------------------------------------------------------------------

def test_alert_texts():
    assert alert_text("Notely", {"type": "new_version", "detail": {"to": "5.3", "notes": "Fixes"}}) \
        == "🚀 <b>Notely</b> shipped v5.3\n\nFixes"
    assert "an update" in alert_text("W", {"type": "new_version", "detail": {"to": "Varies with device"}})
    assert alert_text("N", {"type": "rating_drop", "detail": {"from": 4.4, "to": 4.3}}) \
        == "📉 <b>N</b> rating fell 4.4 → 4.3 this week"
    spike = alert_text("N", {"type": "one_star_spike", "detail": {"share": 0.45, "baseline": 0.12, "reviews": 40}},
                       "Sync broke")
    assert "45% of 40 new reviews" in spike and "usually 12%" in spike and spike.endswith("Sync broke")
    assert "&lt;b&gt;" in alert_text("<b>", {"type": "app_removed", "detail": {}})


def test_pending_alerts_go_to_unpaused_watchers_only(conn):
    seed(conn, 1)
    seed(conn, 2, paused=True)
    seed(conn, 3)
    conn.execute("update watchlist set paused = true where chat_id = 3")
    conn.execute("insert into alerts (package, type, dedupe_key, detail) values ('a.app', 'app_removed', 'removed', %s)",
                 (Jsonb({}),))
    items = pending_alerts(conn)
    assert [i.chat_id for i in items] == [1] and "no longer available" in items[0].text

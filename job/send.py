"""Phase 9 — Sending. See PLAN.md → Update, Alert, Global Pause, App Pause.

Cards are uploaded once (silently to the Admin chat, then deleted) to get a
Telegram file_id, and every Watcher receives that file_id. Messages go through
a Pacer: round-robin across chats, at most 25 per second overall and one per
second per chat, with retry on 429 and blocked users marked on 403.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .db import JobRun, Jsonb, log_error
from .telegram import Telegram, TelegramSendError

GLOBAL_PER_SECOND = 25
MAX_ATTEMPTS = 5
CARD_FRESH_HOURS = 30


@dataclass
class Outgoing:
    chat_id: int
    package: str
    kind: str                 # daily | alert
    ref: str                  # card period_start, or alert id
    text: str                 # caption or message text
    file_id: str | None = None
    silent: bool = False
    attempts: int = 0


class Pacer:
    def __init__(self, conn, tg: Telegram, run: JobRun | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.conn, self.tg, self.run, self.sleep, self.clock = conn, tg, run, sleep, clock

    def send_all(self, items: list[Outgoing]) -> dict[str, int]:
        queues: dict[int, deque[Outgoing]] = {}
        for it in items:
            queues.setdefault(it.chat_id, deque()).append(it)
        stats = {"sent": 0, "failed": 0, "blocked": 0, "rate_limited": 0}
        while queues:
            started = self.clock()
            for chat_id in list(queues)[:GLOBAL_PER_SECOND]:
                q = queues[chat_id]
                it = q.popleft()
                outcome = self._send(it)
                if outcome == "retry":
                    q.appendleft(it)
                    stats["rate_limited"] += 1
                elif outcome == "blocked":
                    stats["blocked"] += 1
                    q.clear()  # nothing else will reach a user who blocked the bot
                else:
                    stats[outcome] += 1
                if not q:
                    queues.pop(chat_id)
                else:  # rotate so the next round serves other chats first
                    queues[chat_id] = queues.pop(chat_id)
            if queues:
                self.sleep(max(0.0, 1.0 - (self.clock() - started)))
        if self.run:
            self.run.add("sent", stats["sent"])
            self.run.add("failed", stats["failed"])
            self.run.add("rate_limited", stats["rate_limited"])
        return stats

    def _send(self, it: Outgoing) -> str:
        try:
            if it.file_id:
                self.tg.send_photo(it.chat_id, it.file_id, it.text, silent=it.silent)
            else:
                self.tg.send_message(it.chat_id, it.text, silent=it.silent)
        except TelegramSendError as e:
            if e.status == 429 and it.attempts < MAX_ATTEMPTS:
                it.attempts += 1
                self.sleep(min(e.retry_after or 1, 60))
                return "retry"
            if e.blocked:
                self.conn.execute("update users set blocked = true where chat_id = %s", (it.chat_id,))
                self.conn.execute("insert into events (chat_id, event) values (%s, 'bot_blocked')", (it.chat_id,))
                self._record(it, "blocked")
                return "blocked"
            log_error(self.conn, "telegram", it.package, f"chat {it.chat_id}: {e}")
            self._record(it, "failed")
            return "failed"
        self._record(it, "sent")
        event = "update_sent" if it.kind != "alert" else "alert_sent"
        self.conn.execute("insert into events (chat_id, event, props) values (%s, %s, %s)",
                          (it.chat_id, event, Jsonb({"package": it.package})))
        return "sent"

    def _record(self, it: Outgoing, status: str) -> None:
        self.conn.execute(
            """insert into deliveries (chat_id, package, kind, ref, status) values (%s, %s, %s, %s, %s)
               on conflict (chat_id, package, kind, ref) do update set status = excluded.status, sent_at = now()""",
            (it.chat_id, it.package, it.kind, it.ref, status),
        )


def silence_after_first(items: list[Outgoing]) -> list[Outgoing]:
    """The phone buzzes once per user per run."""
    seen: set[int] = set()
    for it in items:
        it.silent = it.chat_id in seen
        seen.add(it.chat_id)
    return items


# --- Card upload ------------------------------------------------------------------

def upload_card(conn, tg: Telegram, package: str, period: str, period_start, png: bytes, caption: str) -> str:
    """Upload once to the Admin chat, keep the file_id, delete the message."""
    store_chat = int(os.environ.get("CARD_STORE_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID") or 0)
    file_id, message_id = tg.upload_photo(store_chat, png)
    tg.delete_message(store_chat, message_id)
    conn.execute(
        """insert into cards (package, period, period_start, telegram_file_id, caption) values (%s, %s, %s, %s, %s)
           on conflict (package, period, period_start) do update set
             telegram_file_id = excluded.telegram_file_id, caption = excluded.caption, created_at = now()""",
        (package, period, period_start, file_id, caption),
    )
    return file_id


# --- Daily Updates at each User's Digest Time ----------------------------------------

def due_updates(conn, now: datetime | None = None) -> list[Outgoing]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = conn.execute(
        """select u.chat_id, u.timezone, to_char(u.digest_time, 'HH24:MI') as digest_time,
                  c.package, c.period_start::text as ref, c.telegram_file_id, c.caption
           from users u
           join watchlist w on w.chat_id = u.chat_id and not w.paused
           join lateral (
             select * from cards c where c.package = w.package and c.period = 'daily'
               and c.telegram_file_id is not null and c.created_at >= %(fresh)s
             order by c.period_start desc limit 1) c on true
           where not u.paused and not u.blocked
             and not exists (select 1 from deliveries d where d.chat_id = u.chat_id and d.package = c.package
                             and d.kind = 'daily' and d.ref = c.period_start::text)
           order by u.chat_id, w.added_at""",
        {"fresh": now - timedelta(hours=CARD_FRESH_HOURS)},
    ).fetchall()
    out = []
    for r in rows:
        try:
            local = now.astimezone(ZoneInfo(r["timezone"]))
        except Exception:  # noqa: BLE001 — bad timezone: fall back to the default
            local = now.astimezone(ZoneInfo("Asia/Kolkata"))
        if local.strftime("%H:%M") >= r["digest_time"]:
            out.append(Outgoing(r["chat_id"], r["package"], "daily", r["ref"], r["caption"] or "",
                                file_id=r["telegram_file_id"]))
    return silence_after_first(out)


# --- Alerts ------------------------------------------------------------------------

def alert_text(name: str, a: dict, verdict: str | None = None) -> str:
    from html import escape as e

    d = a["detail"]
    n = f"<b>{e(name)}</b>"
    if a["type"] == "new_version":
        head = f"🚀 {n} shipped {('v' + e(d['to'])) if d.get('to') and d['to'] != 'Varies with device' else 'an update'}"
        notes = (d.get("notes") or "").strip()
        return head + (f"\n\n{e(notes[:500])}" if notes else "")
    if a["type"] == "rating_drop":
        return f"📉 {n} rating fell {d['from']:.1f} → {d['to']:.1f} this week"
    if a["type"] == "one_star_spike":
        text = (f"😡 1★ reviews spiking for {n}: {d['share'] * 100:.0f}% of {d['reviews']} new reviews today "
                f"(usually {d['baseline'] * 100:.0f}%)")
        return text + (f"\n\n{e(verdict)}" if verdict else "")
    if a["type"] == "app_removed":
        return f"⚠️ {n} is no longer available on Google Play"
    return f"{n}: {e(a['type'])}"


def pending_alerts(conn) -> list[Outgoing]:
    rows = conn.execute(
        """select a.id, a.package, a.type, a.detail, coalesce(ap.name, a.package) as name, s.verdict,
                  u.chat_id
           from alerts a
           join apps ap on ap.package = a.package
           join watchlist w on w.package = a.package and not w.paused
           join users u on u.chat_id = w.chat_id and not u.paused and not u.blocked
           left join summaries s on s.package = a.package and s.period = 'daily'
                                and s.period_start = a.detected_at::date
           where a.sent_at is null and a.detected_at >= now() - interval '2 days'
             and not exists (select 1 from deliveries d where d.chat_id = u.chat_id and d.package = a.package
                             and d.kind = 'alert' and d.ref = a.id::text)
           order by a.id, u.chat_id""").fetchall()
    return [Outgoing(r["chat_id"], r["package"], "alert", str(r["id"]), alert_text(r["name"], r, r["verdict"]))
            for r in rows]


def mark_alerts_sent(conn) -> None:
    conn.execute("update alerts set sent_at = now() where sent_at is null and detected_at >= now() - interval '2 days'")

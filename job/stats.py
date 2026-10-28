"""Daily Stats: one row per day in daily_stats, used by the Dashboard history.

Days follow the bot's home timezone (Asia/Kolkata). "Active" means the User did
something; "reached" means the User received an Update or Alert.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

HOME_TZ = ZoneInfo("Asia/Kolkata")
SYSTEM_EVENTS = ("update_sent", "alert_sent", "bot_blocked", "bot_unblocked", "admin_denied")
REACH_EVENTS = ("update_sent", "alert_sent")


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), HOME_TZ)
    return start, start + timedelta(days=1)


def retention(conn, day: date, n: int) -> float | None:
    """Share of Users who signed up n days before `day` and still receive Updates."""
    start, end = _bounds(day - timedelta(days=n))
    row = conn.execute(
        """select count(*)::int as cohort,
                  count(*) filter (where not blocked and not paused)::int as kept
           from users where created_at >= %s and created_at < %s""", (start, end)).fetchone()
    return round(row["kept"] / row["cohort"], 4) if row["cohort"] else None


def build(conn, day: date) -> dict:
    start, end = _bounds(day)
    p = {"s": start, "e": end, "system": list(SYSTEM_EVENTS), "reach": list(REACH_EVENTS)}
    row = conn.execute(
        """select
             (select count(*) from users where created_at < %(e)s)::int as users_total,
             (select count(*) from users where created_at >= %(s)s and created_at < %(e)s)::int as users_new,
             (select count(distinct chat_id) from events
                where ts >= %(s)s and ts < %(e)s and chat_id is not null and event <> all(%(system)s))::int as dau_active,
             (select count(distinct chat_id) from events
                where ts >= %(s)s and ts < %(e)s and event = any(%(reach)s))::int as dau_reached,
             (select count(*) from users where blocked)::int as blocked_total,
             (select count(*) from events where ts >= %(s)s and ts < %(e)s and event = 'bot_blocked')::int as blocked_new,
             (select count(distinct package) from watchlist)::int as apps_watched,
             (select count(*) from events where ts >= %(s)s and ts < %(e)s and event = 'update_sent')::int as updates_sent,
             (select count(*) from events where ts >= %(s)s and ts < %(e)s and event = 'alert_sent')::int as alerts_sent,
             (select coalesce(sum(cost_usd), 0) from spend where ts >= %(s)s and ts < %(e)s) as claude_cost_usd""",
        p).fetchone()
    row = dict(row) | {"date": day, "d1_retention": retention(conn, day, 1), "d7_retention": retention(conn, day, 7),
                       "d30_retention": retention(conn, day, 30)}
    cols = list(row)
    conn.execute(
        f"""insert into daily_stats ({", ".join(cols)}) values ({", ".join("%(" + c + ")s" for c in cols)})
            on conflict (date) do update set {", ".join(f"{c} = excluded.{c}" for c in cols if c != "date")}""",
        row)
    return row

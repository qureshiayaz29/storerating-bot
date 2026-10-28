"""Retention (PLAN.md → Data model → Retention).

events, errors: 90 days (Daily Stats keep the aggregates)
deliveries, on_demand: 30 days
app_snapshots: daily for 90 days, then one row per ISO week (the latest)
"""

from __future__ import annotations

from datetime import date, timedelta


def run(conn, today: date) -> dict[str, int]:
    out = {}
    out["events"] = conn.execute("delete from events where ts < now() - interval '90 days'").rowcount
    out["errors"] = conn.execute("delete from errors where ts < now() - interval '90 days'").rowcount
    out["deliveries"] = conn.execute("delete from deliveries where sent_at < now() - interval '30 days'").rowcount
    out["on_demand"] = conn.execute("delete from on_demand where requested_at < now() - interval '30 days'").rowcount
    out["app_snapshots"] = conn.execute(
        """delete from app_snapshots s using (
             select package, date, row_number() over (
               partition by package, date_trunc('week', date) order by date desc) as rn
             from app_snapshots where date < %s) old
           where s.package = old.package and s.date = old.date and old.rn > 1""",
        (today - timedelta(days=90),)).rowcount
    return out

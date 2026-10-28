"""Phase 5 — Alert Events. See PLAN.md → Alert Event.

Detection is a pure function of recent Snapshots, so it is easy to test.
Alerts are numeric; Claude only explains a spike later, it never decides one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .db import Jsonb

RATING_DROP = 0.1          # drop from the 7-day high
SPIKE_MIN_REVIEWS = 20     # need this many new reviews today to call a spike
SPIKE_MIN_SHARE = 0.30     # today's 1★ share must reach at least this...
SPIKE_FACTOR = 2.0         # ...and be this many times the all-time share
VARIES = {"Varies with device", None, ""}


@dataclass(frozen=True)
class AlertEvent:
    package: str
    type: str          # new_version | rating_drop | one_star_spike | app_removed
    dedupe_key: str
    detail: dict


def detect(package: str, snapshots: list[dict], today: date, removed: bool = False) -> list[AlertEvent]:
    """snapshots: this App's rows, oldest first, ending with today's if it exists."""
    if removed:
        return [AlertEvent(package, "app_removed", "removed", {})]
    snaps = [s for s in snapshots if s["date"] <= today]
    if not snaps or snaps[-1]["date"] != today:
        return []
    cur, prev = snaps[-1], (snaps[-2] if len(snaps) > 1 else None)
    events: list[AlertEvent] = []

    # New release: version change, or store update time moved for "Varies with device" apps.
    if prev is not None:
        if cur.get("version") not in VARIES and prev.get("version") not in VARIES and cur["version"] != prev["version"]:
            events.append(AlertEvent(package, "new_version", f"v:{cur['version']}", {
                "from": prev["version"], "to": cur["version"], "notes": cur.get("release_notes")}))
        elif (cur.get("version") in VARIES and cur.get("updated_at") and prev.get("updated_at")
              and cur["updated_at"] != prev["updated_at"]):
            events.append(AlertEvent(package, "new_version", f"u:{_d(cur['updated_at'])}", {
                "from": None, "to": cur.get("version"), "updated_at": _d(cur["updated_at"]),
                "notes": cur.get("release_notes")}))

    # Rating drop: today at least 0.1 below the highest rating of the last 7 days.
    window = [s for s in snaps if s["date"] >= today - timedelta(days=7) and s.get("rating") is not None]
    if cur.get("rating") is not None and len(window) > 1:
        high = max(float(s["rating"]) for s in window)
        if high - float(cur["rating"]) >= RATING_DROP - 1e-9:
            # One alert per ISO week: a slow slide does not alert every day.
            week = today.isocalendar()
            events.append(AlertEvent(package, "rating_drop", f"w:{week.year}-{week.week:02d}", {
                "from": high, "to": float(cur["rating"])}))

    # 1★ spike: today's new reviews are far more negative than the app's all-time mix.
    n, share, base = cur.get("new_reviews"), cur.get("new_one_star_share"), cur.get("one_star_share")
    if n is not None and n >= SPIKE_MIN_REVIEWS and share is not None:
        share, base = float(share), float(base or 0)
        if share >= SPIKE_MIN_SHARE and share >= SPIKE_FACTOR * base:
            events.append(AlertEvent(package, "one_star_spike", f"d:{today.isoformat()}", {
                "share": share, "baseline": base, "reviews": n}))
    return events


def recent_snapshots(conn, package: str, today: date, days: int = 8) -> list[dict]:
    return conn.execute(
        """select date, version, rating, updated_at, release_notes, one_star_share, new_reviews, new_one_star_share
           from app_snapshots where package = %s and date between %s and %s order by date""",
        (package, today - timedelta(days=days), today),
    ).fetchall()


def record(conn, events: list[AlertEvent]) -> list[AlertEvent]:
    """Insert events; return only the ones not seen before (dedupe_key)."""
    new = []
    for e in events:
        row = conn.execute(
            """insert into alerts (package, type, dedupe_key, detail) values (%s, %s, %s, %s)
               on conflict (package, type, dedupe_key) do nothing returning id""",
            (e.package, e.type, e.dedupe_key, Jsonb(e.detail)),
        ).fetchone()
        if row:
            new.append(e)
    return new


def run_detection(conn, today: date, outcomes) -> list[AlertEvent]:
    found: list[AlertEvent] = []
    for out in outcomes:
        if out.removed:
            found += record(conn, detect(out.package, [], today, removed=True))
        elif out.ok:
            found += record(conn, detect(out.package, recent_snapshots(conn, out.package, today), today))
    return found


def _d(v) -> str:
    return v.date().isoformat() if hasattr(v, "date") else str(v)[:10]

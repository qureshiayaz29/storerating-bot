"""Monthly and Quarterly Reports. See PLAN.md → Monthly Report, Quarterly Report.

python -m job.reports — runs on the 1st of each month after the daily job.
Monthly: the previous calendar month, one chart point per day.
Quarterly (1 Jan, Apr, Jul, Oct): the previous quarter, one point per week.

Themes come from stored Summaries, never raw reviews: one small Claude call per
App folds the period's daily (or monthly) summaries into persisting, fixed and
new themes. Cards are delivered by job.deliver at each User's Digest Time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from . import budget, summaries
from .card.render import CardData, Renderer, caption, card_html
from .db import JobRun, connect, job_run, log_error, log_exception
from .send import upload_card
from .telegram import Telegram

MIN_SUMMARIES = 2
STATUS_LABEL = {"persisting": "persisting", "fixed": "fixed", "new": "new"}

PROMPT = """App: {name} ({package})
Period: {label}
Below are the review summaries for this app during the period, oldest first.
Each line: date | complaints | praise | verdict

{lines}

Combine them into the period's main themes. For complaints, mark each theme:
"persisting" (seen early and still seen at the end), "fixed" (seen early, gone by the end),
or "new" (only appears late in the period).
Return JSON exactly in this shape:
{{"complaints": [{{"theme": "<4–6 words>", "share": <0–100, rough share of complaint mentions>, "status": "persisting|fixed|new", "example": "<max 12 words>"}}],
  "praise": [{{"theme": "<4–6 words>", "share": <0–100>, "example": "<max 12 words>"}}],
  "verdict": "<one line, max 90 characters, how the period went and why>"}}
Up to 3 items each, most important first."""


@dataclass(frozen=True)
class Period:
    kind: str     # monthly | quarterly
    start: date
    end: date     # inclusive

    @property
    def label(self) -> str:
        if self.kind == "monthly":
            return f"{self.start:%B %Y}"
        q = (self.start.month - 1) // 3 + 1
        return f"Q{q} · {self.start:%b}–{self.end:%b %Y}"

    @property
    def source_period(self) -> str:
        return "daily" if self.kind == "monthly" else "monthly"


def previous_month(today: date) -> Period:
    end = today.replace(day=1) - timedelta(days=1)
    return Period("monthly", end.replace(day=1), end)


def previous_quarter(today: date) -> Period:
    q_start_month = 3 * ((today.month - 1) // 3) + 1
    end = today.replace(month=q_start_month, day=1) - timedelta(days=1)
    start = end.replace(month=end.month - 2, day=1)
    return Period("quarterly", start, end)


def periods_due(today: date) -> list[Period]:
    if today.day != 1:
        return []
    out = [previous_month(today)]
    if today.month in (1, 4, 7, 10):
        out.append(previous_quarter(today))
    return out


def eligible_apps(conn, p: Period) -> list[str]:
    """Watched Apps tracked from early in the period: a report only covers what we saw."""
    grace = 7 if p.kind == "monthly" else 14
    rows = conn.execute(
        """select a.package from apps a
           where not a.removed and exists (select 1 from watchlist w where w.package = a.package)
             and (select min(date) from app_snapshots s where s.package = a.package) <= %s
             and exists (select 1 from app_snapshots s where s.package = a.package and s.date between %s and %s)
           order by a.package""",
        (p.start + timedelta(days=grace), p.start, p.end)).fetchall()
    return [r["package"] for r in rows]


# --- card data --------------------------------------------------------------------

def report_card(conn, package: str, p: Period, themes: summaries.Summary | None) -> tuple[CardData, list, list]:
    app = conn.execute("select name, developer, icon_url from apps where package = %s", (package,)).fetchone()
    snaps = conn.execute(
        """select date, rating::float as rating, version, new_reviews from app_snapshots
           where package = %s and date between %s and %s order by date""", (package, p.start, p.end)).fetchall()
    rated = [s for s in snaps if s["rating"] is not None]
    first, last = (rated[0], rated[-1]) if rated else (None, None)
    low = min(rated, key=lambda s: s["rating"]) if rated else None

    if p.kind == "monthly":
        days = (p.end - p.start).days + 1
        by_day = {s["date"]: s["rating"] for s in rated}
        points = [by_day.get(p.start + timedelta(days=i)) for i in range(days)]
        index = lambda d: (d - p.start).days
        mid = p.start + timedelta(days=days // 2)
        x_labels = [(0, f"{p.start.day} {p.start:%b}"), (days // 2, f"{mid.day} {mid:%b}"),
                    (days - 1, f"{p.end.day} {p.end:%b}")]
        chart_title = f"{p.start:%B}"
    else:
        weeks = ((p.end - p.start).days // 7) + 1
        buckets: dict[int, list[float]] = {}
        for s in rated:
            buckets.setdefault((s["date"] - p.start).days // 7, []).append(s["rating"])
        points = [round(sum(buckets[w]) / len(buckets[w]), 3) if w in buckets else None for w in range(weeks)]
        index = lambda d: min(weeks - 1, (d - p.start).days // 7)
        months = [p.start, (p.start.replace(day=28) + timedelta(days=4)).replace(day=1)]
        months.append((months[1].replace(day=28) + timedelta(days=4)).replace(day=1))
        x_labels = [(index(m), f"{m:%b}") for m in months] + [(weeks - 1, "End")]
        chart_title = f"{weeks} weeks"

    releases = []
    for r in conn.execute(
            """select detected_at::date as d, detail->>'to' as v from alerts where package = %s
               and type = 'new_version' and detected_at::date between %s and %s order by detected_at""",
            (package, p.start, p.end)).fetchall():
        releases.append((index(r["d"]), r["v"] if r["v"] and r["v"] != "Varies with device" else "update"))

    reviews = sum(s["new_reviews"] or 0 for s in snaps)
    rating = last["rating"] if last else None
    delta = (last["rating"] - first["rating"]) if first and last and first is not last else None
    stats = [(f"{reviews:,}" if reviews else "–", "reviews"), (str(len(releases)), "releases"),
             (f"{low['rating']:.2f}" if low else "–", "lowest")]
    complaints = themes.complaints if themes else []
    praise = themes.praise if themes else []
    chips = [("bad", _chip(c)) for c in complaints[:2]] + [("good", _chip(x)) for x in praise[:1]]
    sub = (f"{p.start.day} {p.start:%b} → {p.end.day} {p.end:%b}" +
           (f" · low of {low['rating']:.2f} on {low['date'].day} {low['date']:%b}" if low else ""))
    verdict = (themes.verdict if themes and themes.verdict else _fallback(first, last, len(releases)))
    d = CardData(period="Monthly" if p.kind == "monthly" else "Quarterly", period_label=p.label,
                 name=app["name"] or package, developer=app["developer"], version=last["version"] if last else None,
                 icon_url=app["icon_url"], rating=rating, delta=delta, subline=sub, stats=stats,
                 chart_title=chart_title, points=points, x_labels=x_labels, releases=releases, verdict=verdict,
                 chips=chips)
    return d, complaints, praise


def _chip(item: dict) -> str:
    status = item.get("status")
    if status:
        return f"{item['theme']} ({STATUS_LABEL[status]})"
    return item["theme"] + (f" ~{item['share']}%" if item.get("share") is not None else "")


def _fallback(first, last, releases: int) -> str:
    if not first or not last:
        return "Not enough data for this period yet."
    change = last["rating"] - first["rating"]
    trend = "steady" if abs(change) < 0.05 else ("down" if change < 0 else "up")
    rel = f", {releases} release{'s' if releases != 1 else ''}" if releases else ""
    return f"Rating {trend} {first['rating']:.1f} → {last['rating']:.1f}{rel}."


# --- themes ------------------------------------------------------------------------

def source_lines(conn, package: str, p: Period) -> list[str]:
    rows = conn.execute(
        """select period_start, complaints, praise, verdict from summaries
           where package = %s and period = %s and period_start between %s and %s order by period_start""",
        (package, p.source_period, p.start, p.end)).fetchall()

    def fmt(items):
        return "; ".join(f"{i['theme']}" + (f" ~{i['share']}%" if i.get("share") is not None else "") for i in items) or "-"

    return [f"{r['period_start']} | {fmt(r['complaints'])} | {fmt(r['praise'])} | {r['verdict'] or '-'}" for r in rows]


def themes_for(conn, client, run: JobRun, package: str, name: str, p: Period,
               now: datetime | None = None) -> summaries.Summary | None:
    lines = source_lines(conn, package, p)
    if client is None or len(lines) < MIN_SUMMARIES or not budget.claude_enabled():
        return None
    prompt = PROMPT.format(name=name, package=package, label=p.label, lines="\n".join(lines))
    est = budget.cost(budget.estimate_tokens(summaries.SYSTEM + prompt), summaries.MAX_TOKENS, batch=False)
    if not budget.can_spend(conn, est, now):
        return None
    try:
        message = client.messages.create(**summaries._params(prompt))
    except Exception as e:  # noqa: BLE001
        log_error(conn, "claude", package, f"report {p.kind}: {type(e).__name__}: {e}")
        return None
    u = message.usage
    budget.record_spend(conn, "report", package, u.input_tokens, u.output_tokens, batch=False)
    run.add("claude_calls")
    run.add("claude_tokens_in", u.input_tokens)
    run.add("claude_tokens_out", u.output_tokens)
    try:
        s = summaries.parse(summaries._text(message))
    except (ValueError, json.JSONDecodeError) as e:
        log_error(conn, "claude", package, f"report {p.kind}: unparseable: {e}")
        return None
    summaries.store(conn, package, p.start, s, period=p.kind)
    return s


# --- run -----------------------------------------------------------------------------

def run_reports(conn, tg: Telegram, client, run: JobRun, today: date, renderer_factory=Renderer,
                now: datetime | None = None) -> dict[str, int]:
    made: dict[str, int] = {}
    due = periods_due(today)
    if not due:
        return made
    with renderer_factory() as r:
        for p in due:
            made[p.kind] = 0
            for pkg in eligible_apps(conn, p):
                try:
                    name = conn.execute("select coalesce(name, package) as n from apps where package = %s",
                                        (pkg,)).fetchone()["n"]
                    themes = themes_for(conn, client, run, pkg, name, p, now)
                    d, complaints, praise = report_card(conn, pkg, p, themes)
                    upload_card(conn, tg, pkg, p.kind, p.start, r.render(card_html(d)), caption(d, complaints, praise))
                    run.add("cards_rendered")
                    made[p.kind] += 1
                except Exception as e:  # noqa: BLE001 — one App must not stop the rest
                    log_exception(conn, "render", pkg, e)
    return made


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set: skipping (secrets not configured yet)")
        return
    from .daily import anthropic_client, job_date

    today = date.fromisoformat(os.environ["REPORT_DATE"]) if os.environ.get("REPORT_DATE") else job_date()
    if not periods_due(today):
        print(f"{today}: no report due")
        return
    conn = connect()
    with job_run(conn, "reports") as run:
        made = run_reports(conn, Telegram(), anthropic_client(), run, today,
                           now=datetime.now(timezone.utc))
    print(f"{today}: report cards {made}; delivered by job.deliver at each user's digest time")


if __name__ == "__main__":
    main()

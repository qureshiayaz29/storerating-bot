"""Daily Job entry point: python -m job.daily

Runs at 05:00 IST from .github/workflows/daily.yml:
  health check → fetch → alert detection → summaries → cards → alerts → budget alerts.
Updates themselves are delivered later by job.deliver at each User's Digest Time.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import admin_report, alerts, budget, cleanup, fetch, health, platform_usage, reports, stats, summaries
from .card.render import Renderer, card_html, daily_caption, daily_card
from .db import JobRun, connect, job_run, log_exception
from .send import Pacer, mark_alerts_sent, pending_alerts, upload_card
from .sources import registry
from .telegram import Telegram, admin_alert

HOME_TZ = ZoneInfo("Asia/Kolkata")  # snapshot dates follow the bot's home timezone


def job_date(now: datetime | None = None) -> date:
    return (now or datetime.now(HOME_TZ)).astimezone(HOME_TZ).date()


def changed(conn, out: fetch.FetchOutcome, today: date, alert_pkgs: set[str], summarised: set[str]) -> bool:
    """An App gets a card when something happened: first run, alert, summary, new reviews or a rating move."""
    if not out.ok:
        return False
    if out.first_run or out.package in alert_pkgs or out.package in summarised or out.reviews:
        return True
    rows = conn.execute(
        "select rating from app_snapshots where package = %s and date <= %s order by date desc limit 2",
        (out.package, today)).fetchall()
    return len(rows) == 2 and rows[0]["rating"] != rows[1]["rating"]


def make_cards(conn, tg: Telegram, run: JobRun, outcomes, today: date, alert_pkgs, summarised) -> int:
    todo = [o for o in outcomes if changed(conn, o, today, alert_pkgs, summarised)]
    if not todo:
        return 0
    made = 0
    with Renderer() as r:
        for out in todo:
            try:
                d, complaints, praise = daily_card(conn, out.package, today)
                upload_card(conn, tg, out.package, "daily", today, r.render(card_html(d)), daily_caption(d, complaints, praise))
                made += 1
            except Exception as e:  # noqa: BLE001 — one card must not stop the rest
                log_exception(conn, "render", out.package, e)
    run.add("cards_rendered", made)
    return made


def already_ran(conn, today: date) -> bool:
    """True when a successful Daily Job already ran for today (Asia/Kolkata), so a backup trigger is a no-op."""
    return conn.execute(
        """select 1 from job_runs where workflow = 'daily' and success
           and (started_at at time zone 'Asia/Kolkata')::date = %s limit 1""", (today,)).fetchone() is not None


def anthropic_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    return anthropic.Anthropic()


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set: skipping (secrets not configured yet)")
        return
    conn = connect()
    tg = Telegram()
    today = job_date()
    if already_ran(conn, today) and os.environ.get("FORCE", "").lower() != "true":
        print(f"Daily Job already ran for {today}: skipping (run with force to repeat)")
        return
    deadline = time.monotonic() + 60 * float(os.environ.get("SUMMARY_DEADLINE_MINUTES", "240"))
    active, fallback = registry.active_source(), registry.fallback_source()

    with job_run(conn, "daily") as run:
        canaries = [health.CANARY]
        if conn.execute("select 1 from watchlist where package like 'ios:%' limit 1").fetchone():
            canaries.append(health.APP_STORE_CANARY)
        for canary in canaries:
            ok, msg = health.check(active, canary)
            print(msg)
            if not ok:
                admin_alert(f"🩺 Store Source health check failed\n{msg}", tg)

        outcomes = fetch.run_fetch(conn, today, active, fallback, run)
        if any(o.used_fallback for o in outcomes):
            admin_alert(f"🔁 Fallback Source used for {sum(o.used_fallback for o in outcomes)} apps today", tg)

        new_alerts = alerts.run_detection(conn, today, outcomes)

        client = anthropic_client()
        done: set[str] = set()
        if client is not None:
            res = summaries.run_summaries(conn, client, outcomes, today, run, deadline=deadline)
            done = set(res.done)
            print(f"Summaries: {len(res.done)} done, {len(res.skipped_budget)} skipped for budget, "
                  f"{len(res.skipped_few_reviews)} with too few reviews, {len(res.failed)} failed")
        else:
            print("Summaries skipped: ANTHROPIC_API_KEY not set")

        cards = make_cards(conn, tg, run, outcomes, today, {a.package for a in new_alerts}, done)
        alert_stats = Pacer(conn, tg, run).send_all(pending_alerts(conn))
        mark_alerts_sent(conn)
        budget.check_alerts(conn, tg=tg)
        stats.build(conn, today - timedelta(days=1))  # yesterday is complete
        stats.build(conn, today)
        platform_usage.run(conn, today, tg)
        print(f"Cleanup: {cleanup.run(conn, today)}")
        print(f"{today}: {len(outcomes)} apps, {len(new_alerts)} alerts, {cards} cards, alert messages {alert_stats}")

    # On the 1st: monthly (and quarterly) report cards, delivered by job.deliver at digest time.
    if reports.periods_due(today):
        with job_run(conn, "reports") as rrun:
            print(f"Reports: {reports.run_reports(conn, tg, client, rrun, today)}")
    # On Mondays: the weekly Admin report.
    if admin_report.is_due(today):
        admin_report.send(conn, tg, today)


if __name__ == "__main__":
    main()

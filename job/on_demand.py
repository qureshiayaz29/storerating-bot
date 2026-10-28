"""On-demand Run: python -m job.on_demand

Started by the Webhook Function through repository_dispatch when a User adds
an App that has no Card today. Waits 60 seconds so several adds share one run,
then for each pending App: fetch, summarise its latest reviews (standard call,
within budget), render, upload and send to the Users who asked.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from . import summaries
from .card.render import Renderer, caption, card_html, daily_card
from .daily import anthropic_client, job_date
from .db import JobRun, connect, job_run, log_exception
from .fetch import AppToFetch, fetch_app, save
from .send import Outgoing, Pacer, upload_card
from .sources import registry
from .telegram import Telegram

WAIT_SECONDS = int(os.environ.get("ON_DEMAND_WAIT_SECONDS", "60"))
CARD_FRESH = timedelta(hours=30)


def pending(conn) -> dict[str, list[int]]:
    rows = conn.execute(
        """select o.package, o.chat_id from on_demand o
           where o.status = 'pending' and o.requested_at >= now() - interval '2 hours'
           order by o.requested_at""").fetchall()
    out: dict[str, list[int]] = {}
    for r in rows:
        out.setdefault(r["package"], [])
        if r["chat_id"] not in out[r["package"]]:
            out[r["package"]].append(r["chat_id"])
    return out


def _app(conn, pkg: str) -> AppToFetch:
    r = conn.execute(
        """select (select count(*) from watchlist where package = %(p)s)::int as watchers,
                  c.last_review_id, c.last_review_at::text as last_review_at
           from (select 1) one left join app_cursor c on c.package = %(p)s""", {"p": pkg}).fetchone()
    return AppToFetch(pkg, r["watchers"], r["last_review_id"], r["last_review_at"])


def _fresh_card(conn, pkg: str, now: datetime):
    return conn.execute(
        """select telegram_file_id as file_id, coalesce(caption, '') as caption, period_start::text as ref
           from cards where package = %s and period = 'daily' and telegram_file_id is not null
             and created_at >= %s order by period_start desc limit 1""", (pkg, now - CARD_FRESH)).fetchone()


def run_on_demand(conn, tg: Telegram, client, run: JobRun, renderer_factory=Renderer,
                  now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    today = job_date(now)
    work = pending(conn)
    if not work:
        return {}
    active, fallback = registry.active_source(), registry.fallback_source()
    results: dict[str, str] = {}
    outgoing: list[Outgoing] = []
    with renderer_factory() as renderer:
        for pkg, chats in work.items():
            try:
                card = _fresh_card(conn, pkg, now)
                if card is None:
                    out = fetch_app(_app(conn, pkg), active, fallback, conn)
                    save(conn, today, out)
                    run.add("apps_fetched" if out.ok else "apps_failed")
                    if not out.ok:
                        raise RuntimeError(f"fetch failed: {out.error}")
                    if client is not None:
                        req = summaries.prepare(out, batch=False, allow_first_run=True)
                        if req is not None:
                            res = summaries.SummaryRun()
                            summaries.standard_call(conn, client, run, res, today, req, "on_demand", now)
                    d, complaints, praise = daily_card(conn, pkg, today)
                    cap = caption(d, complaints, praise)
                    file_id = upload_card(conn, tg, pkg, "daily", today, renderer.render(card_html(d)), cap)
                    run.add("cards_rendered")
                    card = {"file_id": file_id, "caption": cap, "ref": today.isoformat()}
                for chat in chats:
                    outgoing.append(Outgoing(chat, pkg, "daily", card["ref"], card["caption"], file_id=card["file_id"]))
                results[pkg] = "done"
            except Exception as e:  # noqa: BLE001 — one App must not stop the others
                log_exception(conn, "render", pkg, e)
                results[pkg] = "failed"
    # Only send to Users who still watch the App and are not paused.
    allowed = {(r["chat_id"], r["package"]) for r in conn.execute(
        """select w.chat_id, w.package from watchlist w join users u using (chat_id)
           where not u.paused and not u.blocked and not w.paused""").fetchall()}
    Pacer(conn, tg, run).send_all([o for o in outgoing if (o.chat_id, o.package) in allowed])
    for pkg, status in results.items():
        conn.execute("update on_demand set status = %s where package = %s and status = 'pending'", (status, pkg))
    return results


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set: skipping (secrets not configured yet)")
        return
    time.sleep(WAIT_SECONDS)  # let adds from the last minute join this run
    conn = connect()
    if not pending(conn):
        print("Nothing pending")
        return
    with job_run(conn, "on_demand") as run:
        results = run_on_demand(conn, Telegram(), anthropic_client(), run)
    print(f"On-demand: {results}")


if __name__ == "__main__":
    main()

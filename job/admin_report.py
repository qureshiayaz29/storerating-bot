"""Weekly Admin report (PLAN.md → Admin): every Monday a card plus caption to
ADMIN_CHAT_ID covering the last 7 complete days against the 7 before them.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from datetime import date, timedelta

from .card.render import BOT_NAME, H, W, Renderer
from .telegram import Telegram, admin_alert

e = html.escape


def is_due(today: date) -> bool:
    return today.weekday() == 0


@dataclass
class Week:
    start: date
    end: date               # inclusive
    users_total: int
    new_users: int
    reached_avg: float
    active_avg: float
    reached_prev: float
    new_prev: int
    blocked_new: int
    updates: int
    alerts: int
    spend: float
    spend_prev: float
    d7: float | None
    reached_daily: list[int]
    top_apps: list[tuple[str, int]]
    limits: list[tuple[str, float]]   # name, percent
    errors: int
    failed_runs: int


def collect(conn, today: date) -> Week:
    end = today - timedelta(days=1)
    start = end - timedelta(days=6)
    prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=1)

    def agg(a: date, b: date) -> dict:
        return conn.execute(
            """select coalesce(avg(dau_reached), 0)::float as reached, coalesce(avg(dau_active), 0)::float as active,
                      coalesce(sum(users_new), 0)::int as new, coalesce(sum(blocked_new), 0)::int as blocked,
                      coalesce(sum(updates_sent), 0)::int as updates, coalesce(sum(alerts_sent), 0)::int as alerts,
                      coalesce(sum(claude_cost_usd), 0)::float as spend
               from daily_stats where date between %s and %s""", (a, b)).fetchone()

    cur, prev = agg(start, end), agg(prev_start, prev_end)
    days = {r["date"]: r["dau_reached"] or 0 for r in conn.execute(
        "select date, dau_reached from daily_stats where date between %s and %s", (start, end)).fetchall()}
    d7 = conn.execute("select avg(d7_retention)::float as v from daily_stats where date between %s and %s",
                      (start, end)).fetchone()["v"]
    total = conn.execute("select count(*)::int as n from users").fetchone()["n"]
    top = conn.execute(
        """select coalesce(a.name, w.package) as name, count(*)::int as n from watchlist w join apps a using (package)
           group by 1 order by n desc limit 3""").fetchall()
    limits = [("Claude budget", 0.0)]
    b = conn.execute(
        """select (select value::float from config where key = 'monthly_budget_usd') as budget,
                  coalesce(sum(cost_usd), 0)::float as spent from spend where ts >= date_trunc('month', now())""").fetchone()
    if b["budget"]:
        limits = [("Claude budget", 100 * b["spent"] / b["budget"])]
    for r in conn.execute(
            """select provider, resource, used::float as used, "limit"::float as lim from platform_usage
               where date = (select max(date) from platform_usage) order by provider, resource""").fetchall():
        if r["lim"]:
            limits.append((f"{r['provider']} {r['resource'].replace('_', ' ')}", 100 * r["used"] / r["lim"]))
    errors = conn.execute("select count(*)::int as n from errors where ts >= %s and ts < %s",
                          (start, end + timedelta(days=1))).fetchone()["n"]
    failed = conn.execute("select count(*)::int as n from job_runs where success = false and started_at >= %s",
                          (start,)).fetchone()["n"]
    return Week(start, end, total, cur["new"], cur["reached"], cur["active"], prev["reached"], prev["new"],
                cur["blocked"], cur["updates"], cur["alerts"], cur["spend"], prev["spend"], d7,
                [days.get(start + timedelta(days=i), 0) for i in range(7)],
                [(r["name"], r["n"]) for r in top], limits, errors, failed)


def _change(cur: float, prev: float) -> str:
    if not prev:
        return "new" if cur else "–"
    pct = round(100 * (cur - prev) / prev)
    return f"{'▲' if pct > 0 else '▼' if pct < 0 else '●'} {abs(pct)}%"


def caption_text(w: Week) -> str:
    lines = [
        f"<b>{BOT_NAME} weekly</b> · {w.start:%d %b} – {w.end:%d %b}",
        f"👥 {w.users_total} users · +{w.new_users} new ({_change(w.new_users, w.new_prev)})",
        f"📬 {w.reached_avg:.0f} reached/day ({_change(w.reached_avg, w.reached_prev)}) · {w.active_avg:.0f} active/day",
        f"📨 {w.updates} updates · {w.alerts} alerts · {w.blocked_new} blocked the bot",
        f"🔁 D7 retention {w.d7 * 100:.0f}%" if w.d7 is not None else "🔁 D7 retention: not enough data",
        f"💰 Claude ${w.spend:.2f} ({_change(w.spend, w.spend_prev)})",
        f"⚠️ {w.errors} errors · {w.failed_runs} failed runs" if (w.errors or w.failed_runs) else "✅ No errors, no failed runs",
    ]
    hot = [f"{n} {p:.0f}%" for n, p in w.limits if p >= 70]
    if hot:
        lines.append("🟠 Near limits: " + ", ".join(hot))
    return "\n".join(lines)[:1024]


def card_html(w: Week) -> str:
    mx = max([1, *w.reached_daily])
    bars = "".join(
        f'<div class="b"><i style="height:{max(2, 100 * v / mx):.0f}%"></i><span>{(w.start + timedelta(days=i)):%a}</span></div>'
        for i, v in enumerate(w.reached_daily))
    tiles = [(str(w.users_total), "users", f"+{w.new_users} this week"),
             (f"{w.reached_avg:.0f}", "reached / day", _change(w.reached_avg, w.reached_prev)),
             (f"{w.d7 * 100:.0f}%" if w.d7 is not None else "–", "D7 retention", ""),
             (f"${w.spend:.2f}", "Claude spend", _change(w.spend, w.spend_prev))]
    tiles_html = "".join(f'<div class="t"><b>{e(v)}</b><span>{e(l)}</span><em>{e(c)}</em></div>' for v, l, c in tiles)
    limits = "".join(
        f'<div class="l"><span>{e(n)}</span><div class="bar {"r" if p >= 90 else "a" if p >= 70 else "g"}">'
        f'<i style="width:{min(100, p):.0f}%"></i></div><b>{p:.0f}%</b></div>' for n, p in w.limits[:5])
    apps = "".join(f"<li>{e(n)} <span>{c}</span></li>" for n, c in w.top_apps) or "<li>No apps yet</li>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;height:{H}px;background:#0e1219;color:#eef1f6;font-family:Inter,"Noto Sans",sans-serif;overflow:hidden}}
.c{{position:absolute;inset:0;padding:34px 44px 40px;display:flex;flex-direction:column;background:radial-gradient(900px 500px at 100% 0%,#1d2433 0%,#0e1219 60%)}}
.top{{display:flex;justify-content:space-between;align-items:center;font-size:22px;font-weight:700}}
.pill{{font-size:13px;letter-spacing:1.4px;padding:7px 14px;border-radius:99px;background:#1f2633;color:#9fe7c8;border:1px solid #2c3546;margin-right:12px}}
.date{{color:#9aa4b6;font-size:17px;font-weight:500}}
.tiles{{display:flex;gap:14px;margin-top:26px}}
.t{{flex:1;background:#161c27;border:1px solid #232b3a;border-radius:16px;padding:16px 18px}}
.t b{{display:block;font-size:34px;letter-spacing:-1px}} .t span{{color:#8b95a8;font-size:14px}}
.t em{{display:block;font-style:normal;color:#9fe7c8;font-size:13px;margin-top:4px;min-height:16px}}
.row{{display:flex;gap:18px;margin-top:18px;flex:1;min-height:0}}
.box{{background:#151a23;border:1px solid #232b3a;border-radius:16px;padding:16px 18px}}
.box h3{{font-size:14px;color:#9aa4b6;font-weight:600;margin-bottom:10px}}
.chart{{flex:1.3;display:flex;flex-direction:column}} .bars{{display:flex;gap:10px;flex:1;min-height:0;align-items:flex-end}}
.b{{flex:1;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:6px}}
.b i{{display:block;width:100%;background:#34d399;border-radius:6px 6px 2px 2px}} .b span{{color:#7d8799;font-size:12px}}
.side{{flex:1;display:flex;flex-direction:column;gap:18px}} .side .box:last-child{{flex:1}}
.l{{display:flex;align-items:center;gap:10px;font-size:13px;margin-bottom:8px}} .l span{{width:150px;color:#c9d1e0}}
.l b{{width:40px;text-align:right;font-size:13px}}
.bar{{flex:1;height:8px;background:#232b3a;border-radius:4px;overflow:hidden}} .bar i{{display:block;height:100%}}
.g i{{background:#34d399}} .a i{{background:#fbbf24}} .r i{{background:#f87171}}
ul{{list-style:none;font-size:15px}} li{{display:flex;justify-content:space-between;padding:3px 0}} li span{{color:#9aa4b6}}
</style></head><body><div class="c">
<div class="top"><div>{BOT_NAME} · Admin</div><div><span class="pill">WEEKLY</span><span class="date">{w.start:%d %b} – {w.end:%d %b %Y}</span></div></div>
<div class="tiles">{tiles_html}</div>
<div class="row"><div class="box chart"><h3>Users reached per day</h3><div class="bars">{bars}</div></div>
<div class="side"><div class="box"><h3>Limits</h3>{limits}</div><div class="box"><h3>Most watched</h3><ul>{apps}</ul></div></div></div>
</div></body></html>"""


def send(conn, tg: Telegram, today: date, renderer_factory=Renderer) -> bool:
    """Render and send the weekly card to the Admin. Never raises."""
    admin = os.environ.get("ADMIN_CHAT_ID")
    if not admin:
        print("Weekly admin report skipped: ADMIN_CHAT_ID not set")
        return False
    try:
        w = collect(conn, today)
        with renderer_factory() as r:
            png = r.render(card_html(w))
        tg._multipart("sendPhoto", {"chat_id": int(admin), "caption": caption_text(w), "parse_mode": "HTML"},
                      "photo", "weekly.png", png)
        return True
    except Exception as ex:  # noqa: BLE001
        admin_alert(f"Weekly admin report failed: {type(ex).__name__}: {ex}", tg)
        return False

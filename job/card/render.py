"""Phase 8 — Cards. See PLAN.md → Card, Update.

One 1200×630 PNG per App per period, plus a caption of at most 1,024
characters. Everything taken from the Play Store or from Claude is escaped:
app names and review themes are untrusted text.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

W, H = 1200, 630
CAPTION_LIMIT = 1024
BOT_NAME = "StoreRating"


@dataclass
class CardData:
    period: str                     # Daily | Monthly | Quarterly
    period_label: str               # "29 Oct 2026"
    name: str
    developer: str | None
    version: str | None
    icon_url: str | None
    rating: float | None
    delta: float | None             # vs previous period
    subline: str
    stats: list[tuple[str, str]]    # up to 3 (value, label)
    chart_title: str
    points: list[float | None]      # rating per step, oldest first
    x_labels: list[tuple[int, str]]
    releases: list[tuple[int, str]]
    verdict: str
    verdict_muted: str = ""
    chips: list[tuple[str, str]] = field(default_factory=list)  # (bad|good, text)


e = html.escape


def chart_svg(points: list[float | None], x_labels, releases, w=640, h=250) -> str:
    vals = [(i, v) for i, v in enumerate(points) if v is not None]
    if len(vals) < 2:
        return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
                f'<text x="{w/2}" y="{h/2}" text-anchor="middle" fill="#7d8799" font-size="16">'
                "Trend appears after 2 days of tracking</text></svg>")
    pl, pr, pt, pb = 44, 14, 14, 30
    ys = [v for _, v in vals]
    lo = math.floor((min(ys) - 0.05) * 10) / 10
    hi = math.ceil((max(ys) + 0.05) * 10) / 10
    if hi - lo < 0.2:
        lo, hi = lo - 0.1, hi + 0.1
    n = len(points)
    trend = vals[-1][1] - vals[0][1]
    col = "#34d399" if trend >= 0.05 else "#ff6b5e" if trend <= -0.05 else "#9aa4b6"
    X = lambda i: pl + (w - pl - pr) * i / max(1, n - 1)
    Y = lambda v: pt + (h - pt - pb) * (hi - v) / (hi - lo)
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
           f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{col}" '
           f'stop-opacity=".28"/><stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient></defs>']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        out.append(f'<line x1="{pl}" x2="{w-pr}" y1="{y:.1f}" y2="{y:.1f}" stroke="#2a3140"/>'
                   f'<text x="{pl-8}" y="{y+4:.1f}" text-anchor="end" fill="#7d8799" font-size="12">{v:.1f}</text>')
    for i, tag in releases:
        x = X(i)
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pt}" y2="{h-pb}" stroke="#5b6478" stroke-dasharray="3 4"/>'
                   f'<rect x="{x-24:.1f}" y="{pt}" width="48" height="18" rx="9" fill="#232a38"/>'
                   f'<text x="{x:.1f}" y="{pt+13}" text-anchor="middle" fill="#c9d1e0" font-size="11" '
                   f'font-weight="600">{e(tag[:7])}</text>')
    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in vals)
    out.append(f'<polygon points="{X(vals[0][0]):.1f},{h-pb} {line} {X(vals[-1][0]):.1f},{h-pb}" fill="url(#g)"/>')
    out.append(f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="3" '
               'stroke-linejoin="round" stroke-linecap="round"/>')
    lo_i, lo_v = min(vals, key=lambda t: t[1])
    out.append(f'<circle cx="{X(lo_i):.1f}" cy="{Y(lo_v):.1f}" r="4" fill="#151a23" stroke="{col}" stroke-width="2"/>')
    out.append(f'<circle cx="{X(vals[-1][0]):.1f}" cy="{Y(vals[-1][1]):.1f}" r="5" fill="{col}" '
               'stroke="#151a23" stroke-width="2"/>')
    for i, label in x_labels:
        anchor = "end" if i == n - 1 else "start" if i == 0 else "middle"
        out.append(f'<text x="{X(i):.1f}" y="{h-8}" text-anchor="{anchor}" fill="#7d8799" font-size="12">{e(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def card_html(d: CardData) -> str:
    rating = f"{d.rating:.1f}" if d.rating is not None else "–"
    if d.delta is None or abs(d.delta) < 0.05:
        delta = '<div class="delta flat">● steady</div>' if d.rating is not None else ""
    elif d.delta < 0:
        delta = f'<div class="delta down">▼ {abs(d.delta):.1f}</div>'
    else:
        delta = f'<div class="delta up">▲ {d.delta:.1f}</div>'
    icon = (f'<img class="icon" src="{e(d.icon_url, quote=True)}" alt="">' if d.icon_url
            else f'<div class="icon letter">{e((d.name or "?")[:1].upper())}</div>')
    stats = "".join(f'<div class="stat"><div class="sv">{e(v)}</div><div class="sl">{e(l)}</div></div>'
                    for v, l in d.stats[:3])
    chips = "".join(f'<div class="chip {"good" if k == "good" else "bad"}"><span class="dot"></span>{e(t)}</div>'
                    for k, t in d.chips[:3])
    sub = " · ".join(x for x in (d.developer, f"v{d.version}" if d.version else None) if x)
    muted = f" <span>{e(d.verdict_muted)}</span>" if d.verdict_muted else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;height:{H}px;background:#0e1219;font-family:Inter,"Noto Sans","Noto Color Emoji",sans-serif;color:#eef1f6;overflow:hidden}}
.card{{position:absolute;inset:0;padding:34px 44px;background:radial-gradient(900px 500px at 100% 0%,#1d2433 0%,#0e1219 60%)}}
.top{{display:flex;justify-content:space-between;align-items:center}}
.brand{{display:flex;align-items:center;gap:12px;font-weight:700;font-size:22px}}
.logo{{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#34d399,#0ea5e9);display:grid;place-items:center}}
.period{{display:flex;align-items:center;gap:12px}}
.pill{{font-size:13px;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;padding:7px 14px;border-radius:99px;background:#1f2633;color:#9fe7c8;border:1px solid #2c3546}}
.date{{color:#9aa4b6;font-size:17px;font-weight:500}}
.main{{display:flex;gap:40px;margin-top:28px}}
.left{{width:410px;display:flex;flex-direction:column}}
.app{{display:flex;gap:16px;align-items:center}}
.icon{{width:68px;height:68px;border-radius:18px;object-fit:cover;flex:none}}
.letter{{background:linear-gradient(140deg,#a78bfa,#6366f1);display:grid;place-items:center;font-size:34px;font-weight:800}}
.an{{font-size:27px;font-weight:700;letter-spacing:-.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:320px}}
.ad{{color:#8b95a8;font-size:15px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:320px}}
.rating{{display:flex;align-items:baseline;gap:14px;margin-top:26px}}
.big{{font-size:76px;font-weight:800;letter-spacing:-3px;line-height:1}}
.star{{color:#fbbf24;font-size:44px}}
.delta{{font-size:22px;font-weight:700;padding:5px 12px;border-radius:10px}}
.down{{color:#ff6b5e;background:#3a1d1f}} .up{{color:#34d399;background:#123026}} .flat{{color:#9aa4b6;background:#1f2633}}
.cap{{color:#8b95a8;font-size:15px;margin-top:6px}}
.stats{{display:flex;gap:10px;margin-top:22px}}
.stat{{flex:1;background:#161c27;border:1px solid #232b3a;border-radius:14px;padding:12px 14px;min-width:0}}
.sv{{font-size:21px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.sl{{color:#8b95a8;font-size:12.5px;margin-top:2px}}
.right{{flex:1;background:#151a23;border:1px solid #232b3a;border-radius:20px;padding:18px 20px 8px}}
.ct{{display:flex;justify-content:space-between;color:#9aa4b6;font-size:14px;font-weight:600;margin-bottom:6px}}
.ct b{{color:#eef1f6}}
.bottom{{position:absolute;left:44px;right:44px;bottom:30px}}
.verdict{{font-size:20px;font-weight:600;margin-bottom:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.verdict span{{color:#8b95a8;font-weight:500}}
.chips{{display:flex;gap:10px;overflow:hidden}}
.chip{{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:500;padding:9px 14px;border-radius:12px;background:#161c27;border:1px solid #232b3a;white-space:nowrap;max-width:360px;overflow:hidden;text-overflow:ellipsis}}
.dot{{width:9px;height:9px;border-radius:50%;flex:none}}
.bad .dot{{background:#ff6b5e}} .good .dot{{background:#34d399}}
</style></head><body><div class="card">
<div class="top"><div class="brand"><div class="logo"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#06221a" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="2,13 7,13 10,5 14,19 17,11 22,11"/></svg></div>{BOT_NAME}</div>
<div class="period"><div class="pill">{e(d.period)}</div><div class="date">{e(d.period_label)}</div></div></div>
<div class="main"><div class="left">
<div class="app">{icon}<div style="min-width:0"><div class="an">{e(d.name)}</div><div class="ad">{e(sub)}</div></div></div>
<div class="rating"><div class="big">{rating}</div><div class="star">★</div>{delta}</div>
<div class="cap">{e(d.subline)}</div><div class="stats">{stats}</div></div>
<div class="right"><div class="ct"><span>Rating · <b>{e(d.chart_title)}</b></span><span>┆ release</span></div>
{chart_svg(d.points, d.x_labels, d.releases)}</div></div>
<div class="bottom"><div class="verdict">{e(d.verdict)}{muted}</div><div class="chips">{chips}</div></div>
</div></body></html>"""


def caption(d: CardData, complaints: list[dict], praise: list[dict]) -> str:
    """Telegram HTML caption, at most 1,024 characters."""
    head = [f"<b>{e(d.name)}</b>"]
    if d.rating is not None:
        r = f"{d.rating:.1f}★"
        if d.delta is not None and abs(d.delta) >= 0.05:
            r += f" {'▼' if d.delta < 0 else '▲'}{abs(d.delta):.1f}"
        head.append(r)
    if d.version:
        head.append(f"v{e(d.version)}")
    lines = [" · ".join(head)]
    for c in complaints[:3]:
        lines.append(f"😡 {e(c['theme'])}" + (f" (~{c['share']}%)" if c.get("share") is not None else ""))
    for p in praise[:2]:
        lines.append(f"😍 {e(p['theme'])}" + (f" (~{p['share']}%)" if p.get("share") is not None else ""))
    if d.verdict:
        lines.append(f"\n{e(d.verdict)}")
    text = "\n".join(lines)
    while len(text) > CAPTION_LIMIT and len(lines) > 1:
        lines.pop(-1 if not lines[-1].startswith("\n") else -2)
        text = "\n".join(lines)
    return text[:CAPTION_LIMIT]


# --- building CardData from the database -------------------------------------------

def daily_card(conn, package: str, today: date) -> tuple[CardData, list[dict], list[dict]]:
    app = conn.execute("select name, developer, icon_url from apps where package = %s", (package,)).fetchone()
    start = today - timedelta(days=29)
    snaps = {r["date"]: r for r in conn.execute(
        """select date, rating, version, new_reviews, new_one_star_share from app_snapshots
           where package = %s and date between %s and %s""", (package, start, today)).fetchall()}
    days = [start + timedelta(days=i) for i in range(30)]
    points = [float(snaps[d]["rating"]) if d in snaps and snaps[d]["rating"] is not None else None for d in days]
    cur = snaps.get(today) or {}
    prev = snaps.get(today - timedelta(days=1)) or {}
    rating = float(cur["rating"]) if cur.get("rating") is not None else None
    delta = (rating - float(prev["rating"])) if rating is not None and prev.get("rating") is not None else None

    releases = []
    for r in conn.execute(
            """select detected_at::date as d, detail->>'to' as v from alerts
               where package = %s and type = 'new_version' and detected_at::date between %s and %s""",
            (package, start, today)).fetchall():
        tag = r["v"] if r["v"] and r["v"] != "Varies with device" else "update"
        releases.append(((r["d"] - start).days, tag))

    s = conn.execute("""select complaints, praise, verdict from summaries
                        where package = %s and period = 'daily' and period_start = %s""", (package, today)).fetchone()
    complaints, praise = (s["complaints"], s["praise"]) if s else ([], [])
    n = cur.get("new_reviews")
    share = cur.get("new_one_star_share")
    stats = [(f"{n:,}" if n is not None else "–", "new reviews"),
             (f"{float(share) * 100:.0f}%" if share is not None else "–", "1★ share today")]
    if cur.get("version"):
        stats.append((f"v{cur['version']}" if cur["version"] != "Varies with device" else "varies", "version"))
    chips = [("bad", _chip(c)) for c in complaints[:2]] + [("good", _chip(p)) for p in praise[:1]]
    verdict = (s and s["verdict"]) or _fallback_verdict(rating, delta, n)
    d = CardData(
        period="Daily", period_label=f"{today.day} {today:%b %Y}", name=app["name"] or package,
        developer=app["developer"], version=cur.get("version"), icon_url=app["icon_url"], rating=rating,
        delta=delta, subline="vs yesterday" + (f" · {n:,} new reviews" if n else ""), stats=stats,
        chart_title="last 30 days", points=points,
        x_labels=[(0, f"{start.day} {start:%b}"), (14, f"{days[14].day} {days[14]:%b}"), (29, "Today")],
        releases=releases, verdict=verdict, chips=chips)
    return d, complaints, praise


def _chip(item: dict) -> str:
    return item["theme"] + (f" ~{item['share']}%" if item.get("share") is not None else "")


def _fallback_verdict(rating, delta, n) -> str:
    if rating is None:
        return "Tracking started. Trends appear from tomorrow."
    if delta is not None and abs(delta) >= 0.05:
        return f"Rating {'down' if delta < 0 else 'up'} {abs(delta):.1f} since yesterday."
    if n:
        return f"Rating steady at {rating:.1f}★ with {n:,} new reviews."
    return f"Rating steady at {rating:.1f}★."


# --- rendering -------------------------------------------------------------------

class Renderer:
    """One headless browser for many cards. Use as a context manager."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        return self

    def render(self, html_text: str) -> bytes:
        self._page.set_content(html_text, wait_until="networkidle", timeout=20_000)
        return self._page.screenshot(type="png")

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

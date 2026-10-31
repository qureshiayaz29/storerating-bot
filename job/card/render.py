"""Phase 8 — Cards. See PLAN.md → Card, Update.

One 1200×630 PNG per App per period, plus a caption of at most 1,024
characters. Everything taken from the Play Store or from Claude is escaped:
app names and review themes are untrusted text.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..sources.base import parse_app_key

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
    # Daily caption only:
    today: date | None = None
    store: str = "Google Play"
    released_days: int | None = None          # days since the store's "updated on" date
    new_reviews: int | None = None
    reviews_are_latest: bool = False          # first run: counts cover the latest reviews, not new ones
    star_counts: list[int] | None = None      # 1★..5★
    takeaway: str = ""                        # Claude's verdict, caption only (the card shows `verdict`)
    # Monthly and Quarterly captions only:
    low: tuple[float, date] | None = None
    high: float | None = None
    release_count: int | None = None


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
    dp = 2 if hi - lo < 0.5 else 1   # 4.55 / 4.60, never "4.7" twice
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        out.append(f'<line x1="{pl}" x2="{w-pr}" y1="{y:.1f}" y2="{y:.1f}" stroke="#2a3140"/>'
                   f'<text x="{pl-8}" y="{y+4:.1f}" text-anchor="end" fill="#7d8799" font-size="12">{v:.{dp}f}</text>')
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
    rating = f"{d.rating:.2f}" if d.rating is not None else "–"   # same precision as the caption
    if d.delta is None or abs(d.delta) < 0.005:
        delta = '<div class="delta flat">● steady</div>' if d.rating is not None else ""
    elif d.delta < 0:
        delta = f'<div class="delta down">▼ {abs(d.delta):.2f}</div>'
    else:
        delta = f'<div class="delta up">▲ {d.delta:.2f}</div>'
    icon = (f'<img class="icon" src="{e(d.icon_url, quote=True)}" alt="">' if d.icon_url
            else f'<div class="icon letter">{e((d.name or "?")[:1].upper())}</div>')
    stats = "".join(f'<div class="stat"><div class="sv">{e(v)}</div><div class="sl">{e(l)}</div></div>'
                    for v, l in d.stats[:3])
    chips = "".join(f'<div class="chip {"good" if k == "good" else "bad"}"><span class="dot"></span>{e(t)}</div>'
                    for k, t in d.chips[:3])
    version = f"v{d.version}" if d.version and d.version != VARIES else None
    sub = " · ".join(x for x in (d.store, d.developer, version) if x)
    muted = f" <span>{e(d.verdict_muted)}</span>" if d.verdict_muted else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:{W}px;height:{H}px;background:#0e1219;font-family:Inter,"Noto Sans","Noto Color Emoji",sans-serif;color:#eef1f6;overflow:hidden}}
.card{{position:absolute;inset:0;padding:34px 44px;background:radial-gradient(900px 500px at 100% 0%,#1d2433 0%,#0e1219 60%)}}
.top{{display:flex;justify-content:space-between;align-items:center}}
.brand{{display:flex;align-items:center;gap:12px;font-weight:700;font-size:22px}}

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
<div class="top"><div class="brand"><svg width="38" height="38" viewBox="0 0 512 512"><defs><radialGradient id="lgbg" cx="80%" cy="10%" r="110%"><stop offset="0" stop-color="#1f2a3d"/><stop offset=".65" stop-color="#0e1219"/></radialGradient><linearGradient id="lgg" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#10b981"/><stop offset="1" stop-color="#6ee7b7"/></linearGradient></defs><rect width="512" height="512" rx="112" fill="url(#lgbg)" stroke="#2c3546" stroke-width="12"/><g transform="translate(256 262) scale(.78) translate(-279 -244)"><g fill="#2c3a50"><rect x="112" y="330" width="58" height="82" rx="14"/><rect x="194" y="290" width="58" height="122" rx="14"/><rect x="276" y="266" width="58" height="146" rx="14"/></g><rect x="358" y="214" width="58" height="198" rx="14" fill="url(#lgg)"/><path transform="translate(387 138) scale(.34) translate(-256 -243)" d="M256 92 L303 196 L416 207 L331 283 L356 394 L256 336 L156 394 L181 283 L96 207 L209 196 Z" fill="#6ee7b7" stroke="#6ee7b7" stroke-width="30" stroke-linejoin="round"/></g></svg>{BOT_NAME}</div>
<div class="period"><div class="pill">{e(d.period)}</div><div class="date">{e(d.period_label)}</div></div></div>
<div class="main"><div class="left">
<div class="app">{icon}<div style="min-width:0"><div class="an">{e(d.name)}</div><div class="ad">{e(sub)}</div></div></div>
<div class="rating"><div class="big">{rating}</div><div class="star">★</div>{delta}</div>
<div class="cap">{e(d.subline)}</div><div class="stats">{stats}</div></div>
<div class="right"><div class="ct"><span>Rating · <b>{e(d.chart_title)}</b></span><span>┆ release</span></div>
{chart_svg(d.points, d.x_labels, d.releases)}</div></div>
<div class="bottom"><div class="verdict">{e(d.verdict)}{muted}</div><div class="chips">{chips}</div></div>
</div></body></html>"""


VARIES = "Varies with device"


def visible_len(html_text: str) -> int:
    """Length as Telegram counts it: tags removed, entities decoded, UTF-16 code units."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)).encode("utf-16-le")) // 2


def _tag(item: dict, today: date | None) -> str:
    if item.get("status") == "new":
        return " 🆕"
    if item.get("status") == "persisting" and item.get("since") and today:
        try:
            days = (today - date.fromisoformat(item["since"])).days + 1
        except ValueError:
            return ""
        return f" 🔁 {days} days" if days >= 2 else ""
    return ""


REPORT_TAG = {"persisting": " 🔁 ongoing", "fixed": " ✅ fixed", "new": " 🆕"}


def daily_caption(d: CardData, complaints: list[dict], praise: list[dict]) -> str:
    """The Update caption (Daily, Monthly, Quarterly): rating, review mix, top complaints and praise, takeaway.

    Fits Telegram's 1,024 characters by dropping, in order: example lines, the 3rd complaint,
    the 2nd praise, the takeaway. The star breakdown and numbers always stay. Numbers are never cut mid-line.
    """
    parts: list[tuple[int, str]] = []    # (drop rank: higher goes first; 0 = never, text)

    def add(text: str, rank: int = 0) -> None:
        parts.append((rank, text))

    report = d.period != "Daily"
    add(f"<b>{e(d.name)}</b>")
    meta = [d.store]
    if report:
        meta.append(f"{e(d.period_label)} report")
        if d.version and d.version != VARIES:
            meta.append(f"v{e(d.version)}")
    elif d.version and d.version != VARIES:
        meta.append(f"v{e(d.version)}" + (f", released {_ago(d.released_days)}" if d.released_days is not None else ""))
    elif d.released_days is not None:
        meta.append(f"updated {_ago(d.released_days)}")
    add(" · ".join(meta))
    add("")
    span = {"Monthly": "the month", "Quarterly": "the quarter"}.get(d.period, "")
    if d.rating is not None and report:
        r = f"⭐ <b>{d.rating:.2f}</b>"
        if d.delta is not None and abs(d.delta) >= 0.005:
            r += f" {'▼' if d.delta < 0 else '▲'}{abs(d.delta):.2f} over {span}"
        elif d.delta is not None:
            r += f" · steady over {span}"
        add(r)
        if d.low and d.high is not None:
            add(f"Low {d.low[0]:.2f} on {d.low[1].day} {d.low[1]:%b} · high {d.high:.2f}")
    elif d.rating is not None:
        r = f"⭐ <b>{d.rating:.2f}</b>"
        if d.delta is not None and abs(d.delta) >= 0.005:
            r += f" {'▼' if d.delta < 0 else '▲'}{abs(d.delta):.2f} vs yesterday"
        elif d.delta is not None:
            r += " · no change"
        vals = [p for p in d.points if p is not None]
        if len(vals) >= 7:
            r += f" · 30d {min(vals):.2f}–{max(vals):.2f}"
        add(r)
    if report and (d.new_reviews or d.release_count):
        rel = f" · {d.release_count} release{'s' if d.release_count != 1 else ''}" if d.release_count else ""
        add(f"📝 <b>{d.new_reviews or 0:,} new reviews</b>{rel}")
        if d.star_counts and sum(d.star_counts):
            total = sum(d.star_counts)
            add(" · ".join(f"{s}★ {round(100 * n / total)}%" for s, n in enumerate(d.star_counts, 1)))
    elif d.new_reviews:
        add(f"📝 <b>{'Latest ' if d.reviews_are_latest else ''}{d.new_reviews:,} {'reviews' if d.reviews_are_latest else 'new reviews'}</b>")
        if d.star_counts and sum(d.star_counts):
            total = sum(d.star_counts)
            add(" · ".join(f"{s}★ {round(100 * n / total)}%" for s, n in enumerate(d.star_counts, 1)))  # never dropped
    if complaints:
        add("")
        add("<b>😡 Top complaints</b>")
        for i, c in enumerate(complaints[:3], 1):
            share = f" · {c['share']}%" if c.get("share") is not None else ""
            tag = REPORT_TAG.get(c.get("status"), "") if report else _tag(c, d.today)
            add(f"{i}. <b>{e(c['theme'])}</b>{share}{tag}", 6 if i == 3 else 0)
            if c.get("example") and i <= 2:
                add(f"<i>{e(c['example'])}</i>", 9 if i == 2 else 8)
    if praise:
        add("")
        add("<b>😍 What users like</b>")
        for i, p in enumerate(praise[:2], 1):
            share = f" · {p['share']}%" if p.get("share") is not None else ""
            add(f"{i}. <b>{e(p['theme'])}</b>{share}", 5 if i == 2 else 0)
            if p.get("example") and i == 1:
                add(f"<i>{e(p['example'])}</i>", 10)
    if d.takeaway:
        add("")
        add(f"💡 {e(d.takeaway)}", 2)

    def render(ps: list[tuple[int, str]]) -> str:
        text = "\n".join(t for _, t in ps)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    text = render(parts)
    for rank in sorted({r for r, _ in parts if r}, reverse=True):
        if visible_len(text) <= CAPTION_LIMIT:
            break
        parts = [(r, t) for r, t in parts if r != rank]
        text = render(parts)
    while visible_len(text) > CAPTION_LIMIT and len(parts) > 1:   # very long names or themes
        parts.pop()
        text = render(parts)
    return text


def _ago(days: int) -> str:
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


# --- building CardData from the database -------------------------------------------

def daily_card(conn, package: str, today: date) -> tuple[CardData, list[dict], list[dict]]:
    app = conn.execute("select name, developer, icon_url from apps where package = %s", (package,)).fetchone()
    start = today - timedelta(days=29)
    snaps = {r["date"]: r for r in conn.execute(
        """select date, rating, version, new_reviews, new_one_star_share, new_star_counts, updated_at from app_snapshots
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
    verdict = _fallback_verdict(rating, delta, n)          # card: facts; Claude's take goes in the caption
    takeaway = (s and s["verdict"]) or ""
    d = CardData(
        period="Daily", period_label=f"{today.day} {today:%b %Y}", name=app["name"] or package,
        developer=app["developer"], version=cur.get("version"), icon_url=app["icon_url"], rating=rating,
        store=parse_app_key(package).badge,
        delta=delta, subline="vs yesterday" + (f" · {n:,} new reviews" if n else ""), stats=stats,
        chart_title="last 30 days", points=points,
        x_labels=[(0, f"{start.day} {start:%b}"), (14, f"{days[14].day} {days[14]:%b}"), (29, "Today")],
        releases=releases, verdict=verdict, chips=chips, today=today, takeaway=takeaway,
        released_days=(today - cur["updated_at"].date()).days if cur.get("updated_at") else None,
        new_reviews=n if n is not None else (sum(cur["new_star_counts"]) if cur.get("new_star_counts") else None),
        reviews_are_latest=n is None, star_counts=cur.get("new_star_counts"))
    return d, complaints, praise


def _chip(item: dict) -> str:
    return item["theme"] + (f" ~{item['share']}%" if item.get("share") is not None else "")


def _fallback_verdict(rating, delta, n) -> str:
    if rating is None:
        return "Tracking started. Trends appear from tomorrow."
    if delta is not None and abs(delta) >= 0.05:
        return f"Rating {'down' if delta < 0 else 'up'} {abs(delta):.2f} since yesterday, now {rating:.2f}★."
    if n:
        return f"Rating steady at {rating:.2f}★ with {n:,} new reviews."
    return f"Rating steady at {rating:.2f}★."


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

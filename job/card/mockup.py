import math, random, asyncio
from playwright.async_api import async_playwright

OUT = "docs"

def series(n, start, end, dip_at=None, dip=0.0, seed=1):
    random.seed(seed)
    pts = []
    for i in range(n):
        t = i / (n - 1)
        v = start + (end - start) * t + random.uniform(-0.02, 0.02)
        if dip_at is not None:
            v -= dip * math.exp(-((i - dip_at) ** 2) / (2 * (n / 12) ** 2))
        pts.append(round(v, 2))
    return pts

def chart_svg(vals, labels, releases, w=640, h=250):
    pl, pr, pt, pb = 44, 14, 14, 30
    lo, hi = min(vals) - 0.1, max(vals) + 0.1
    lo, hi = math.floor(lo * 10) / 10, math.ceil(hi * 10) / 10
    n = len(vals)
    X = lambda i: pl + (w - pl - pr) * i / (n - 1)
    Y = lambda v: pt + (h - pt - pb) * (hi - v) / (hi - lo)
    grid = ""
    steps = 4
    for k in range(steps + 1):
        v = lo + (hi - lo) * k / steps
        y = Y(v)
        grid += f'<line x1="{pl}" x2="{w-pr}" y1="{y:.1f}" y2="{y:.1f}" stroke="#2a3140" stroke-width="1"/>'
        grid += f'<text x="{pl-8}" y="{y+4:.1f}" text-anchor="end" fill="#7d8799" font-size="12">{v:.1f}</text>'
    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    area = f"{X(0):.1f},{h-pb} " + line + f" {X(n-1):.1f},{h-pb}"
    rel = ""
    for i, tag in releases:
        rel += f'<line x1="{X(i):.1f}" x2="{X(i):.1f}" y1="{pt}" y2="{h-pb}" stroke="#5b6478" stroke-dasharray="3 4"/>'
        rel += f'<rect x="{X(i)-22:.1f}" y="{pt}" width="44" height="18" rx="9" fill="#232a38"/>'
        rel += f'<text x="{X(i):.1f}" y="{pt+13}" text-anchor="middle" fill="#c9d1e0" font-size="11" font-weight="600">{tag}</text>'
    xl = ""
    for i, lab in labels:
        anc = "end" if i == n - 1 else ("start" if i == 0 else "middle")
        xl += f'<text x="{X(i):.1f}" y="{h-8}" text-anchor="{anc}" fill="#7d8799" font-size="12">{lab}</text>'
    lo_i = vals.index(min(vals))
    end = f'<circle cx="{X(n-1):.1f}" cy="{Y(vals[-1]):.1f}" r="5" fill="#ff6b5e" stroke="#151a23" stroke-width="2"/>'
    low = f'<circle cx="{X(lo_i):.1f}" cy="{Y(vals[lo_i]):.1f}" r="4" fill="#151a23" stroke="#ff6b5e" stroke-width="2"/>'
    return f'''<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff6b5e" stop-opacity=".28"/><stop offset="1" stop-color="#ff6b5e" stop-opacity="0"/></linearGradient></defs>
{grid}{rel}<polygon points="{area}" fill="url(#g)"/><polyline points="{line}" fill="none" stroke="#ff6b5e" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>{low}{end}{xl}</svg>'''

def card(d):
    chips = "".join(
        f'<div class="chip {c}"><span class="dot"></span>{t}</div>' for c, t in d["chips"])
    stats = "".join(
        f'<div class="stat"><div class="sv">{v}</div><div class="sl">{l}</div></div>' for v, l in d["stats"])
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1200px;height:630px;background:#0e1219;font-family:Inter,sans-serif;color:#eef1f6;overflow:hidden}}
.card{{position:absolute;inset:0;padding:34px 44px;background:radial-gradient(900px 500px at 100% 0%,#1d2433 0%,#0e1219 60%)}}
.top{{display:flex;justify-content:space-between;align-items:center}}
.brand{{display:flex;align-items:center;gap:12px;font-weight:700;font-size:22px;letter-spacing:-.3px}}
.logo{{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#34d399,#0ea5e9);display:grid;place-items:center}}
.period{{display:flex;align-items:center;gap:12px}}
.pill{{font-size:13px;font-weight:700;letter-spacing:1.4px;text-transform:uppercase;padding:7px 14px;border-radius:99px;background:#1f2633;color:#9fe7c8;border:1px solid #2c3546}}
.date{{color:#9aa4b6;font-size:17px;font-weight:500}}
.main{{display:flex;gap:40px;margin-top:28px}}
.left{{width:410px;display:flex;flex-direction:column}}
.app{{display:flex;gap:16px;align-items:center}}
.icon{{width:68px;height:68px;border-radius:18px;background:linear-gradient(140deg,#a78bfa,#6366f1);display:grid;place-items:center;font-size:34px;font-weight:800}}
.an{{font-size:27px;font-weight:700;letter-spacing:-.5px}}
.ad{{color:#8b95a8;font-size:15px;margin-top:3px}}
.rating{{display:flex;align-items:baseline;gap:14px;margin-top:26px}}
.big{{font-size:76px;font-weight:800;letter-spacing:-3px;line-height:1}}
.star{{color:#fbbf24;font-size:44px}}
.delta{{font-size:22px;font-weight:700;color:#ff6b5e;background:#3a1d1f;padding:5px 12px;border-radius:10px}}
.cap{{color:#8b95a8;font-size:15px;margin-top:6px}}
.stats{{display:flex;gap:10px;margin-top:22px}}
.stat{{flex:1;background:#161c27;border:1px solid #232b3a;border-radius:14px;padding:12px 14px}}
.sv{{font-size:21px;font-weight:700}}
.sl{{color:#8b95a8;font-size:12.5px;margin-top:2px}}
.right{{flex:1;background:#151a23;border:1px solid #232b3a;border-radius:20px;padding:18px 20px 8px}}
.ct{{display:flex;justify-content:space-between;color:#9aa4b6;font-size:14px;font-weight:600;margin-bottom:6px}}
.ct b{{color:#eef1f6}}
.bottom{{position:absolute;left:44px;right:44px;bottom:30px}}
.verdict{{font-size:20px;font-weight:600;letter-spacing:-.2px;margin-bottom:14px}}
.verdict span{{color:#8b95a8;font-weight:500}}
.chips{{display:flex;gap:10px}}
.chip{{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:500;padding:9px 14px;border-radius:12px;background:#161c27;border:1px solid #232b3a;white-space:nowrap}}
.dot{{width:9px;height:9px;border-radius:50%}}
.bad .dot{{background:#ff6b5e}} .good .dot{{background:#34d399}}
.foot{{position:absolute;right:44px;bottom:36px;color:#5d6679;font-size:13px}}
</style></head><body><div class="card">
<div class="top">
 <div class="brand"><div class="logo"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#06221a" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="2,13 7,13 10,5 14,19 17,11 22,11"/></svg></div>StoreRating</div>
 <div class="period"><div class="pill">{d["period"]}</div><div class="date">{d["date"]}</div></div>
</div>
<div class="main">
 <div class="left">
  <div class="app"><div class="icon">N</div><div><div class="an">Notely – Notes &amp; Tasks</div><div class="ad">Notely Labs · v{d["version"]}</div></div></div>
  <div class="rating"><div class="big">{d["rating"]}</div><div class="star">★</div><div class="delta">▼ {d["delta"]}</div></div>
  <div class="cap">{d["cap"]}</div>
  <div class="stats">{stats}</div>
 </div>
 <div class="right"><div class="ct"><span>Rating · <b>{d["chart_title"]}</b></span><span>┆ release</span></div>{d["chart"]}</div>
</div>
<div class="bottom"><div class="verdict">{d["verdict"]}</div><div class="chips">{chips}</div></div>
</div></body></html>'''

daily_vals = series(30, 4.32, 4.12, dip_at=26, dip=0.08, seed=3)
monthly_vals = series(31, 4.34, 4.12, dip_at=14, dip=0.12, seed=5)
q_vals = series(13, 4.41, 4.12, dip_at=8, dip=0.06, seed=7)

cards = {
 "daily": dict(period="Daily", date="29 Oct 2026", version="5.3.1", rating="4.1", delta="0.1",
   cap="vs yesterday · 1,284 new reviews",
   stats=[("1,284","new reviews"),("46%","1★ share ▲"),("v5.3.1","new today")],
   chart_title="last 30 days", chart=chart_svg(daily_vals,[(0,"30 Sep"),(14,"14 Oct"),(29,"Today")],[(26,"5.3")]),
   verdict="1★ spike after v5.3 — sync bug is back. <span>Login fix landed well.</span>",
   chips=[("bad","Notes not syncing ~41%"),("bad","Widget blank ~18%"),("good","Faster search ~22%")]),
 "monthly": dict(period="Monthly", date="October 2026", version="5.3.1", rating="4.1", delta="0.2",
   cap="1 Oct → 31 Oct · low of 4.08 on 14 Oct",
   stats=[("31.2k","reviews"),("3","releases"),("4.08","lowest")],
   chart_title="October", chart=chart_svg(monthly_vals,[(0,"1 Oct"),(15,"16 Oct"),(30,"31 Oct")],[(5,"5.1"),(13,"5.2"),(26,"5.3")]),
   verdict="Rough month: v5.2 broke sync, v5.3 only half fixed it. <span>Ads complaints down.</span>",
   chips=[("bad","Sync issues (persisting)"),("bad","Battery drain (new)"),("good","Ads (fixed)")]),
 "quarterly": dict(period="Quarterly", date="Q3 · Jul–Sep 2026", version="5.1.0", rating="4.1", delta="0.3",
   cap="start of quarter 4.4 · weekly average",
   stats=[("88.7k","reviews"),("7","releases"),("−0.3","net change")],
   chart_title="13 weeks", chart=chart_svg(q_vals,[(0,"Jul"),(4,"Aug"),(8,"Sep"),(12,"End")],[(3,"4.8"),(8,"5.0")]),
   verdict="Steady slide since v5.0 redesign. <span>Users miss the old editor.</span>",
   chips=[("bad","New editor UX ~35%"),("bad","Sync reliability ~27%"),("good","Dark mode ~19%")]),
}

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width":1200,"height":630}, device_scale_factor=2)
        for k, d in cards.items():
            await pg.set_content(card(d))
            await pg.wait_for_timeout(200)
            await pg.screenshot(path=f"{OUT}/card_{k}.png")
        await b.close()
asyncio.run(main())
print("done")

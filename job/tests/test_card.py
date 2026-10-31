from datetime import date, timedelta

import pytest

from job.card.render import CAPTION_LIMIT, CardData, Renderer, caption, card_html, chart_svg, daily_card
from job.db import Jsonb

TODAY = date(2026, 10, 29)


def data(**kw) -> CardData:
    base = dict(period="Daily", period_label="29 Oct 2026", name="Notely", developer="Notely Labs", version="5.3.1",
                icon_url=None, rating=4.1, delta=-0.1, subline="vs yesterday", stats=[("1,284", "new reviews")],
                chart_title="last 30 days", points=[4.3, 4.2, 4.1], x_labels=[(0, "30 Sep"), (2, "Today")],
                releases=[(1, "5.3")], verdict="Sync bug is back", chips=[("bad", "Sync ~41%")])
    return CardData(**(base | kw))


def test_untrusted_text_is_escaped():
    html_text = card_html(data(name='<script>alert(1)</script>', verdict='"><img src=x onerror=1>',
                               chips=[("bad", "<b>x</b>")], releases=[(1, "<v>")],
                               icon_url='https://x/"onerror="1'))
    assert "<script>alert" not in html_text and "<img src=x" not in html_text and "<b>x</b>" not in html_text
    assert "&lt;script&gt;" in html_text and 'onerror="1' not in html_text


def test_chart_needs_two_points():
    assert "after 2 days" in chart_svg([4.1], [], [])
    assert "<polyline" in chart_svg([4.2, None, 4.1], [(0, "a")], [])


def test_delta_states():
    assert "▼ 0.1" in card_html(data(delta=-0.1))
    assert "▲ 0.2" in card_html(data(delta=0.2))
    assert "steady" in card_html(data(delta=0.0))
    assert "steady" not in card_html(data(rating=None, delta=None))


def test_caption_format_and_limit():
    text = caption(data(), [{"theme": "Notes not syncing", "share": 41}], [{"theme": "Faster search", "share": 22}])
    assert text.splitlines()[0] == "<b>Notely</b> · 4.1★ ▼0.1 · v5.3.1"
    assert "😡 Notes not syncing (~41%)" in text and "😍 Faster search (~22%)" in text
    long = caption(data(verdict="v" * 2000, name="N" * 300), [{"theme": "t" * 500, "share": 1}] * 3, [])
    assert len(long) <= CAPTION_LIMIT


def test_caption_escapes():
    assert "&lt;b&gt;" in caption(data(name="<b>"), [], [])


def test_daily_card_from_database(conn):
    conn.execute("insert into apps (package, name, developer) values ('p', 'Notely', 'Labs')")
    for i, r in enumerate([4.3, 4.25, 4.2]):
        conn.execute("""insert into app_snapshots (package, date, source_adapter, rating, version, new_reviews,
                        new_one_star_share) values ('p', %s, 'stub', %s, '5.3', 120, 0.46)""",
                     (TODAY - timedelta(days=2 - i), r))
    conn.execute("""insert into alerts (package, type, dedupe_key, detail, detected_at)
                    values ('p', 'new_version', 'v:5.3', %s, %s)""", (Jsonb({"to": "5.3"}), TODAY))
    conn.execute("""insert into summaries (package, period, period_start, complaints, praise, verdict)
                    values ('p', 'daily', %s, %s, %s, 'Sync bug is back')""",
                 (TODAY, Jsonb([{"theme": "Sync broken", "share": 41}]), Jsonb([{"theme": "Fast", "share": 20}])))
    d, complaints, praise = daily_card(conn, "p", TODAY)
    assert d.rating == 4.2 and d.delta == pytest.approx(-0.05)
    assert d.points[-3:] == [4.3, 4.25, 4.2] and d.points[0] is None
    assert d.releases == [(29, "5.3")]
    assert d.verdict == "Sync bug is back" and d.chips[0] == ("bad", "Sync broken ~41%")
    assert ("46%", "1★ share today") in d.stats


def test_daily_card_without_summary_has_a_plain_verdict(conn):
    conn.execute("insert into apps (package, name) values ('p', 'App')")
    conn.execute("insert into app_snapshots (package, date, source_adapter, rating) values ('p', %s, 'stub', 4.4)",
                 (TODAY,))
    d, _, _ = daily_card(conn, "p", TODAY)
    assert d.verdict == "Rating steady at 4.4★." and d.chips == []


def _chromium_available() -> bool:
    try:
        with Renderer():
            return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _chromium_available(), reason="Playwright Chromium not installed")
def test_render_produces_a_2x_png():
    with Renderer() as r:
        png = r.render(card_html(data()))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert (width, height) == (2400, 1260)


def test_line_colour_follows_trend():
    assert "#34d399" in chart_svg([4.0, 4.3], [], []) and "#ff6b5e" not in chart_svg([4.0, 4.3], [], [])
    assert 'stroke="#ff6b5e"' in chart_svg([4.3, 4.0], [], [])
    assert 'stroke="#9aa4b6"' in chart_svg([4.3, 4.31], [], [])


# --- daily caption ---------------------------------------------------------------------

from job.card.render import daily_caption, visible_len  # noqa: E402

KITE_BAD = [
    {"theme": "Login and positions blank after account switch", "share": 67, "status": "new", "since": "2026-10-29",
     "example": "positions screen stays empty after switching accounts"},
    {"theme": "Charts fail to load in trading hours", "share": 33, "status": "persisting", "since": "2026-10-27",
     "example": "chart keeps spinning at market open"},
    {"theme": "Pledged shares unavailable for delivery", "share": 33},
]
KITE_GOOD = [{"theme": "Fast, reliable, easy to use", "share": 55, "example": "smoothest trading app"},
             {"theme": "Helpful customer support", "share": 36}]


def kite(**kw) -> CardData:
    base = {"name": "Zerodha Kite - Trade & Invest", "version": "3.11.11", "rating": 4.59, "delta": 0.03,
            "points": [4.55] * 20 + [4.60] * 10, "verdict": "Account-switching and chart bugs drive most complaints.",
            "today": TODAY, "released_days": 12, "new_reviews": 22, "star_counts": [3, 1, 2, 4, 12]}
    return data(**(base | kw))


def test_daily_caption_has_every_section():
    text = daily_caption(kite(), KITE_BAD, KITE_GOOD)
    assert text.startswith("<b>Zerodha Kite - Trade &amp; Invest</b>\nGoogle Play · v3.11.11, released 12 days ago")
    assert "⭐ <b>4.59</b> ▲0.03 vs yesterday · 30d 4.55–4.60" in text
    assert "📝 <b>22 new reviews</b>" in text and "1★ 14% · 2★ 5% · 3★ 9% · 4★ 18% · 5★ 55%" in text
    assert "1. <b>Login and positions blank after account switch</b> · 67% 🆕" in text
    assert "2. <b>Charts fail to load in trading hours</b> · 33% 🔁 3 days" in text
    assert "<i>positions screen stays empty after switching accounts</i>" in text
    assert "😍 What users like" in text and "💡 Account-switching" in text
    assert "Open on Google Play" not in text and "\n\n\n" not in text
    assert visible_len(text) <= CAPTION_LIMIT


def test_daily_caption_hides_varies_with_device_and_first_run_says_latest():
    text = daily_caption(kite(version="Varies with device", new_reviews=500, reviews_are_latest=True, delta=None),
                         [], [])
    assert "Varies" not in text and "Google Play · updated 12 days ago" in text
    assert "📝 <b>Latest 500 reviews</b>" in text and "vs yesterday" not in text


def test_daily_caption_trims_examples_first_and_keeps_numbers():
    long_bad = [dict(c, theme=c["theme"] + " " + "x" * 50, example="y" * 110) for c in KITE_BAD]
    long_good = [dict(p, theme=p["theme"] + " " + "z" * 50, example="w" * 110) for p in KITE_GOOD]
    text = daily_caption(kite(name="N" * 50, verdict="v" * 120), long_bad, long_good)
    assert visible_len(text) <= CAPTION_LIMIT
    assert "w" * 110 not in text and text.count("y" * 110) == 1    # praise and 2nd examples go first, only as needed
    assert "3. <b>Pledged" in text                                 # the 3rd complaint survives when it fits
    assert "⭐ <b>4.59</b>" in text and "1. <b>Login" in text          # never the numbers or the top complaint


def test_visible_len_counts_like_telegram():
    assert visible_len("<b>a &amp; b</b> 😀") == len("a & b ") + 2


def test_worst_case_keeps_star_breakdown_and_fits():
    w = "W"
    bad = [{"theme": w * 60, "share": 100, "status": "persisting", "since": "2026-09-01", "example": w * 120}] * 3
    good = [{"theme": w * 60, "share": 100, "example": w * 120}] * 3
    text = daily_caption(kite(name=w * 50, verdict=w * 120, released_days=365, new_reviews=99999,
                              star_counts=[10, 10, 10, 10, 60]), bad, good)
    assert visible_len(text) <= CAPTION_LIMIT and "1★ 10% · 2★ 10% · 3★ 10% · 4★ 10% · 5★ 60%" in text
    assert "3. <b>" in text and "💡" in text

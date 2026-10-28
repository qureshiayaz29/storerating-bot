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

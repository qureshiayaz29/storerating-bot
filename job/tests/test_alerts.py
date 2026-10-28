from datetime import date, datetime, timedelta, timezone

from job.alerts import detect, record, run_detection
from job.fetch import FetchOutcome

TODAY = date(2026, 10, 29)


def snap(days_ago: int, **kw) -> dict:
    base = {"date": TODAY - timedelta(days=days_ago), "version": "5.2", "rating": 4.3, "updated_at": None,
            "release_notes": None, "one_star_share": 0.1, "new_reviews": 50, "new_one_star_share": 0.1}
    return base | kw


def types(events):
    return [e.type for e in events]


def test_quiet_day_has_no_alerts():
    assert detect("p", [snap(1), snap(0)], TODAY) == []


def test_no_snapshot_today_means_no_alerts():
    assert detect("p", [snap(2), snap(1)], TODAY) == []


def test_new_version():
    events = detect("p", [snap(1), snap(0, version="5.3", release_notes="Fixes")], TODAY)
    assert types(events) == ["new_version"]
    assert events[0].detail == {"from": "5.2", "to": "5.3", "notes": "Fixes"}
    assert events[0].dedupe_key == "v:5.3"


def test_new_release_for_varies_with_device_apps_uses_updated_at():
    v = "Varies with device"
    prev = snap(1, version=v, updated_at=datetime(2026, 10, 20, tzinfo=timezone.utc))
    cur = snap(0, version=v, updated_at=datetime(2026, 10, 28, tzinfo=timezone.utc))
    events = detect("p", [prev, cur], TODAY)
    assert types(events) == ["new_version"] and events[0].dedupe_key == "u:2026-10-28"


def test_first_snapshot_never_alerts_a_release():
    assert types(detect("p", [snap(0, version="9.9")], TODAY)) == []


def test_rating_drop_from_seven_day_high():
    snaps = [snap(6, rating=4.4), snap(3, rating=4.35), snap(0, rating=4.3)]
    events = detect("p", snaps, TODAY)
    assert types(events) == ["rating_drop"]
    assert events[0].detail == {"from": 4.4, "to": 4.3}


def test_small_dip_is_not_a_drop():
    assert detect("p", [snap(2, rating=4.35), snap(0, rating=4.3)], TODAY) == []


def test_drop_older_than_a_week_is_ignored():
    assert detect("p", [snap(9, rating=4.6), snap(0, rating=4.3)], TODAY) == []


def test_one_star_spike():
    events = detect("p", [snap(1), snap(0, new_reviews=40, new_one_star_share=0.45, one_star_share=0.12)], TODAY)
    assert types(events) == ["one_star_spike"]
    assert events[0].detail == {"share": 0.45, "baseline": 0.12, "reviews": 40}


def test_spike_needs_enough_reviews():
    assert detect("p", [snap(0, new_reviews=10, new_one_star_share=0.9)], TODAY) == []


def test_spike_must_be_well_above_baseline():
    assert detect("p", [snap(0, new_reviews=100, new_one_star_share=0.35, one_star_share=0.30)], TODAY) == []


def test_first_run_has_no_review_numbers_and_no_spike():
    assert detect("p", [snap(0, new_reviews=None, new_one_star_share=None)], TODAY) == []


def test_removed_app():
    assert types(detect("p", [], TODAY, removed=True)) == ["app_removed"]


def test_several_alerts_at_once():
    events = detect("p", [snap(1, rating=4.5), snap(0, version="6.0", rating=4.3, new_reviews=30,
                                                     new_one_star_share=0.5, one_star_share=0.1)], TODAY)
    assert sorted(types(events)) == ["new_version", "one_star_spike", "rating_drop"]


# --- database ------------------------------------------------------------------

def test_record_dedupes(conn):
    conn.execute("insert into apps (package, name) values ('p', 'P')")
    events = detect("p", [snap(1), snap(0, version="5.3")], TODAY)
    assert len(record(conn, events)) == 1
    assert record(conn, events) == []
    assert conn.execute("select count(*) as n from alerts").fetchone()["n"] == 1


def test_run_detection_reads_snapshots(conn):
    conn.execute("insert into apps (package, name) values ('p', 'P'), ('gone', 'Gone')")
    for s in (snap(1), snap(0, version="5.3")):
        conn.execute(
            """insert into app_snapshots (package, date, source_adapter, version, rating, one_star_share)
               values ('p', %s, 'stub', %s, %s, %s)""", (s["date"], s["version"], s["rating"], s["one_star_share"]))
    outcomes = [FetchOutcome("p", 1, listing=object()), FetchOutcome("gone", 1, removed=True)]  # type: ignore[arg-type]
    found = run_detection(conn, TODAY, outcomes)
    assert sorted(types(found)) == ["app_removed", "new_version"]

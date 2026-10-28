from datetime import datetime, timezone

import pytest

from job import budget
from job.budget import BudgetState, can_spend, check_alerts, cost, priority_order, record_spend, state
from job.db import get_config, set_config
from job.fetch import FetchOutcome
from job.telegram import Telegram

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)  # day 15 of a 31-day month


def test_cost_uses_haiku_rates_and_batch_discount():
    assert cost(1_000_000, 0, batch=False) == pytest.approx(0.10)
    assert cost(0, 1_000_000, batch=False) == pytest.approx(0.50)
    assert cost(1_000_000, 1_000_000, batch=True) == pytest.approx(0.30)


def s(**kw) -> BudgetState:
    return BudgetState(**({"monthly": 31.0, "spent_month": 0.0, "spent_before_today": 0.0, "spent_today": 0.0,
                           "day": 15, "days_in_month": 31} | kw))


def test_daily_budget_and_rollover():
    st = s(spent_before_today=7.0, spent_month=7.0)
    assert st.daily == pytest.approx(1.0)
    assert st.available_today == pytest.approx(8.0)  # 15 days of allowance minus 7 spent
    assert st.remaining_today == pytest.approx(8.0)


def test_remaining_never_exceeds_the_month():
    st = s(day=31, spent_before_today=30.5, spent_month=30.5)
    assert st.remaining_today == pytest.approx(0.5)


def test_degrade_mode_at_80_percent_of_today():
    # 14 of the first 15 days' allowance already used: $1 available today.
    assert not s(spent_before_today=14.0, spent_today=0.79, spent_month=14.79).degraded
    assert s(spent_before_today=14.0, spent_today=0.8, spent_month=14.8).degraded
    assert s(spent_before_today=15.0, spent_month=15.0).degraded  # nothing available today


def test_rollover_delays_degrade_mode():
    assert not s(spent_today=0.8, spent_month=0.8).degraded  # $15 available on day 15


def test_projection():
    assert s(spent_month=10.0).projected == pytest.approx(10 / 15 * 31)


def test_priority_order_puts_most_watched_first():
    a = FetchOutcome("a", 1, reviews=[1, 2, 3])  # type: ignore[list-item]
    b = FetchOutcome("b", 5)
    c = FetchOutcome("c", 5, reviews=[1])  # type: ignore[list-item]
    assert [o.package for o in priority_order([a, b, c])] == ["c", "b", "a"]


# --- database ------------------------------------------------------------------

def add_spend(conn, usd: float, ts: datetime):
    conn.execute("insert into spend (ts, source, model, cost_usd) values (%s, 'daily', 'm', %s)", (ts, usd))


def test_state_reads_ledger_and_config(conn):
    set_config(conn, "monthly_budget_usd", "31")
    add_spend(conn, 3.0, datetime(2026, 10, 2, tzinfo=timezone.utc))
    add_spend(conn, 0.5, datetime(2026, 10, 15, 1, tzinfo=timezone.utc))
    add_spend(conn, 9.0, datetime(2026, 9, 30, tzinfo=timezone.utc))  # last month: ignored
    st = state(conn, NOW)
    assert (st.spent_month, st.spent_before_today, st.spent_today) == (3.5, 3.0, 0.5)
    assert st.remaining_today == pytest.approx(15 - 3.0 - 0.5)


def test_record_spend_writes_the_ledger(conn):
    c = record_spend(conn, "daily", "p", 2_000_000, 400_000, batch=True)
    assert c == pytest.approx(0.2)
    row = conn.execute("select * from spend").fetchone()
    assert (row["batch"], row["tokens_in"], float(row["cost_usd"])) == (True, 2_000_000, pytest.approx(0.2))


def test_can_spend_and_kill_switch(conn, monkeypatch):
    set_config(conn, "monthly_budget_usd", "31")
    assert can_spend(conn, 1.0, NOW)
    assert not can_spend(conn, 100.0, NOW)
    monkeypatch.setenv("CLAUDE_ENABLED", "false")
    assert not can_spend(conn, 0.01, NOW)


def test_a_one_cent_budget_degrades(conn):
    set_config(conn, "monthly_budget_usd", "0.31")  # $0.01 per day
    add_spend(conn, 0.15, NOW.replace(hour=1))
    assert state(conn, NOW).degraded
    assert not can_spend(conn, 0.0001, NOW)


def test_top_up_alert_once_at_70_percent(conn):
    set_config(conn, "monthly_budget_usd", "30")
    add_spend(conn, 21.0, datetime(2026, 10, 10, tzinfo=timezone.utc))
    tg = Telegram(token=None)
    sent = check_alerts(conn, NOW, tg)
    assert sent == ["budget_alert:2026-10:70"]
    assert check_alerts(conn, NOW, tg) == []
    assert get_config(conn, "budget_alert:2026-10:50") == "30"


def test_raising_the_budget_resets_alerts(conn):
    set_config(conn, "monthly_budget_usd", "30")
    add_spend(conn, 25.0, datetime(2026, 10, 10, tzinfo=timezone.utc))
    tg = Telegram(token=None)
    assert check_alerts(conn, NOW, tg) == ["budget_alert:2026-10:80"]
    set_config(conn, "monthly_budget_usd", "35")
    assert check_alerts(conn, NOW, tg) == ["budget_alert:2026-10:70"]


def test_pace_alert_below_70_percent(conn, monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_CHAT_ID", "999")
    set_config(conn, "monthly_budget_usd", "30")
    add_spend(conn, 16.0, datetime(2026, 10, 5, tzinfo=timezone.utc))  # 53%, projected 33
    tg = Telegram(token=None)
    sent = check_alerts(conn, NOW, tg)
    assert "pace_alert:last" in sent
    assert any("on pace" in m["text"] for m in tg.sent)
    assert "pace_alert:last" not in check_alerts(conn, NOW, tg)  # not again within 3 days


def test_alert_text_has_the_top_up_steps():
    text = budget.status_text(s(monthly=30, spent_month=21), "💰 StoreRating budget at 70% — time to top up?")
    assert "Spent $21.00 of $30.00 · $9.00 left" in text
    assert "console.anthropic.com/settings/billing" in text and "/setbudget" in text

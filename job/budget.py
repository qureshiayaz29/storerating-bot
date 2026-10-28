"""Phase 6 — Budget Guardrails. See PLAN.md → Monthly Budget, Daily Budget,
Spend Ledger, Priority Order, Degrade Mode, Kill Switch, Top-up Alert, Pace Alert.

All amounts are USD. Days and months are UTC.
"""

from __future__ import annotations

import calendar
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import get_config, set_config
from .telegram import Telegram, admin_alert

MODEL = "claude-haiku-5-5"
# Haiku 5.5 per million tokens, prompts up to 100K tokens (platform.claude.com, checked 29 Oct 2026).
RATE_IN = 0.10
RATE_OUT = 0.50
BATCH_DISCOUNT = 0.5
MAX_PROMPT_TOKENS = 100_000      # above this the higher price tier applies
DEGRADE_AT = 0.8
THRESHOLDS = (50, 70, 80, 100)
PACE_EVERY_DAYS = 3
CONSOLE_BILLING = "https://console.anthropic.com/settings/billing"


def cost(tokens_in: int, tokens_out: int, batch: bool) -> float:
    c = (tokens_in * RATE_IN + tokens_out * RATE_OUT) / 1_000_000
    return c * BATCH_DISCOUNT if batch else c


def estimate_tokens(text: str) -> int:
    """Conservative estimate: ~3.5 characters per token, rounded up."""
    return math.ceil(len(text) / 3.5)


def claude_enabled() -> bool:
    return os.environ.get("CLAUDE_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")


@dataclass(frozen=True)
class BudgetState:
    monthly: float
    spent_month: float
    spent_before_today: float
    spent_today: float
    day: int
    days_in_month: int

    @property
    def daily(self) -> float:
        return self.monthly / self.days_in_month

    @property
    def available_today(self) -> float:
        """Today's allowance plus anything unused earlier this month (rollover)."""
        return max(0.0, self.daily * self.day - self.spent_before_today)

    @property
    def remaining_today(self) -> float:
        return max(0.0, min(self.available_today - self.spent_today, self.monthly - self.spent_month))

    @property
    def degraded(self) -> bool:
        return self.available_today <= 0 or self.spent_today >= DEGRADE_AT * self.available_today

    @property
    def pct_month(self) -> float:
        return 100 * self.spent_month / self.monthly if self.monthly else 100.0

    @property
    def projected(self) -> float:
        return self.spent_month / self.day * self.days_in_month


def state(conn, now: datetime | None = None) -> BudgetState:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    row = conn.execute(
        """select coalesce(sum(cost_usd) filter (where ts >= %(m)s), 0)::float as month,
                  coalesce(sum(cost_usd) filter (where ts >= %(m)s and ts < %(d)s), 0)::float as before,
                  coalesce(sum(cost_usd) filter (where ts >= %(d)s), 0)::float as today
           from spend where ts >= %(m)s""",
        {"m": month_start, "d": day_start},
    ).fetchone()
    return BudgetState(
        monthly=float(get_config(conn, "monthly_budget_usd", "30")),
        spent_month=row["month"], spent_before_today=row["before"], spent_today=row["today"],
        day=now.day, days_in_month=calendar.monthrange(now.year, now.month)[1],
    )


def can_spend(conn, estimated_usd: float, now: datetime | None = None) -> bool:
    if not claude_enabled():
        return False
    s = state(conn, now)
    return not s.degraded and estimated_usd <= s.remaining_today


def record_spend(conn, source: str, package: str | None, tokens_in: int, tokens_out: int, batch: bool) -> float:
    c = cost(tokens_in, tokens_out, batch)
    conn.execute(
        """insert into spend (source, package, model, batch, tokens_in, tokens_out, cost_usd)
           values (%s, %s, %s, %s, %s, %s, %s)""",
        (source, package, MODEL, batch, tokens_in, tokens_out, c),
    )
    return c


def priority_order(outcomes):
    """Most Watchers first, then most new reviews: if money runs out, the long tail waits."""
    return sorted(outcomes, key=lambda o: (-o.watchers, -len(o.reviews), o.package))


# --- Alerts -------------------------------------------------------------------

def status_text(s: BudgetState, headline: str) -> str:
    left = max(0.0, s.monthly - s.spent_month)
    return (
        f"{headline}\n"
        f"Spent ${s.spent_month:.2f} of ${s.monthly:.2f} · ${left:.2f} left\n"
        f"{s.days_in_month - s.day} days left · projected ${s.projected:.2f} by month end\n"
        f"Top up: {CONSOLE_BILLING}\n"
        "To spend more: top up, raise the Console limit, then /setbudget &lt;amount&gt;"
    )


def check_alerts(conn, now: datetime | None = None, tg: Telegram | None = None) -> list[str]:
    """Send threshold and pace alerts once each. Returns the alert keys sent."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    s = state(conn, now)
    month = now.strftime("%Y-%m")
    sent = []
    # Highest threshold crossed and not yet sent for the current budget amount.
    for t in sorted(THRESHOLDS, reverse=True):
        if s.pct_month < t:
            continue
        key = f"budget_alert:{month}:{t}"
        if get_config(conn, key) == f"{s.monthly:g}":
            break  # this or a higher one already went out for this budget
        headline = {
            50: "💰 StoreRating budget at 50%",
            70: "💰 StoreRating budget at 70% — time to top up?",
            80: "⚠️ StoreRating budget at 80% — Degrade Mode is close",
            100: "🛑 StoreRating budget at 100% — summaries stopped, updates continue without them",
        }[t]
        admin_alert(status_text(s, headline), tg)
        for lower in THRESHOLDS:
            if lower <= t:
                set_config(conn, f"budget_alert:{month}:{lower}", f"{s.monthly:g}")
        sent.append(key)
        break
    # Pace: on track to overspend, even below 70%.
    if s.pct_month < 70 and s.day >= 3 and s.projected > s.monthly:
        key = "pace_alert:last"
        last = get_config(conn, key)
        if not last or (now.date() - datetime.fromisoformat(last).date()).days >= PACE_EVERY_DAYS:
            admin_alert(status_text(s, "📈 StoreRating is on pace to pass its budget"), tg)
            set_config(conn, key, now.isoformat())
            sent.append(key)
    return sent

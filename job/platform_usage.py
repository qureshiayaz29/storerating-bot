"""Platform Limits: Vercel Usage (self-metered on Hobby) and database size,
compared with free-tier limits stored in config. Alerts at 70% and 90%.
See PLAN.md → Platform Limits, Vercel Usage, Self-Metering, Vercel Usage Alert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .db import get_config, set_config
from .telegram import Telegram, admin_alert

THRESHOLDS = (90, 70)
DEFAULT_LIMITS = {
    "vercel_limit_invocations": "1000000",
    "vercel_limit_active_cpu_hours": "4",
    "vercel_limit_data_transfer_gb": "100",
    "supabase_limit_db_mb": "500",
}


@dataclass(frozen=True)
class Usage:
    provider: str
    resource: str
    used: float
    limit: float
    unit: str
    source: str

    @property
    def pct(self) -> float:
        return 100 * self.used / self.limit if self.limit else 0.0


def _limit(conn, key: str) -> float:
    return float(get_config(conn, key, DEFAULT_LIMITS[key]))


def collect(conn, today: date) -> list[Usage]:
    period_start = today.replace(day=1)
    m = conn.execute(
        """select coalesce(sum(invocations), 0)::float as inv, coalesce(sum(duration_ms_total), 0)::float as ms,
                  coalesce(sum(response_bytes_total), 0)::float as bytes
           from fn_metrics where date >= %s and date <= %s""", (period_start, today)).fetchone()
    size = conn.execute("select pg_database_size(current_database())::float as b").fetchone()["b"]
    return [
        Usage("vercel", "invocations", m["inv"], _limit(conn, "vercel_limit_invocations"), "invocations", "self_metered"),
        # Wall-clock duration is an upper bound for active CPU: alerts fire early, never late.
        Usage("vercel", "active_cpu", round(m["ms"] / 3_600_000, 4), _limit(conn, "vercel_limit_active_cpu_hours"),
              "CPU-hours", "self_metered"),
        Usage("vercel", "data_transfer", round(m["bytes"] / 1e9, 4), _limit(conn, "vercel_limit_data_transfer_gb"),
              "GB", "self_metered"),
        Usage("supabase", "db_size", round(size / 1e6, 1), _limit(conn, "supabase_limit_db_mb"), "MB", "api"),
    ]


def store(conn, today: date, usage: list[Usage]) -> None:
    for u in usage:
        conn.execute(
            """insert into platform_usage (date, provider, resource, used, "limit", unit, source)
               values (%s, %s, %s, %s, %s, %s, %s)
               on conflict (date, provider, resource) do update set used = excluded.used,
                 "limit" = excluded."limit", unit = excluded.unit, source = excluded.source""",
            (today, u.provider, u.resource, u.used, u.limit, u.unit, u.source))


def fmt(v: float) -> str:
    return f"{v:,.0f}" if v >= 100 else f"{v:,.2f}"


def check_alerts(conn, today: date, usage: list[Usage], tg: Telegram | None = None) -> list[str]:
    """Send one alert per resource per threshold per month."""
    sent = []
    month = today.strftime("%Y-%m")
    for u in usage:
        for t in THRESHOLDS:
            if u.pct < t:
                continue
            key = f"platform_alert:{month}:{u.provider}:{u.resource}"
            last = int(get_config(conn, key, "0") or 0)
            if last >= t:
                break
            lines = [f"{'🚨' if t >= 90 else '⚠️'} {u.provider.capitalize()} usage at {t}%",
                     f"{u.resource.replace('_', ' ').capitalize()}: {fmt(u.used)} of {fmt(u.limit)} {u.unit} "
                     f"({u.pct:.0f}%)",
                     f"Source: {'self-metered' if u.source == 'self_metered' else 'measured'}"]
            if u.provider == "vercel":
                lines.append("Over 100% pauses the bot's web parts for up to 30 days.")
            admin_alert("\n".join(lines), tg)
            set_config(conn, key, str(t))
            sent.append(f"{key}:{t}")
            break
    return sent


def run(conn, today: date, tg: Telegram | None = None) -> list[Usage]:
    usage = collect(conn, today)
    store(conn, today, usage)
    check_alerts(conn, today, usage, tg)
    return usage

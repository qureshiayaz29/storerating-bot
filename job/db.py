"""Database access for the Daily Job. Connects as the App Role."""

from __future__ import annotations

import os
import traceback
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect(url: str | None = None) -> psycopg.Connection:
    # prepare_threshold=None: safe behind Supabase's poolers.
    return psycopg.connect(url or os.environ["DATABASE_URL"], row_factory=dict_row,
                           autocommit=True, prepare_threshold=None)


def get_config(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("select value from config where key = %s", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn, key: str, value: str) -> None:
    conn.execute(
        """insert into config (key, value) values (%s, %s)
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (key, value),
    )


def log_error(conn, source: str, package: str | None, message: str, stack: str | None = None) -> None:
    conn.execute(
        "insert into errors (source, package, message, stack) values (%s, %s, %s, %s)",
        (source, package, message[:2000], (stack or "")[:8000] or None),
    )


def log_exception(conn, source: str, package: str | None, exc: BaseException) -> None:
    log_error(conn, source, package, f"{type(exc).__name__}: {exc}",
              "".join(traceback.format_exception(exc)))


class JobRun:
    """Counters for one workflow run, written to job_runs."""

    FIELDS = ("apps_fetched", "apps_failed", "reviews_processed", "claude_calls", "claude_tokens_in",
              "claude_tokens_out", "cards_rendered", "sent", "failed", "rate_limited")

    def __init__(self, conn, workflow: str) -> None:
        self.conn = conn
        self.counts = dict.fromkeys(self.FIELDS, 0)
        self.id = conn.execute("insert into job_runs (workflow) values (%s) returning id", (workflow,)).fetchone()["id"]

    def add(self, field: str, n: int = 1) -> None:
        self.counts[field] += n

    def finish(self, success: bool) -> None:
        sets = ", ".join(f"{f} = %({f})s" for f in self.FIELDS)
        self.conn.execute(
            f"update job_runs set finished_at = now(), success = %(success)s, {sets} where id = %(id)s",
            {**self.counts, "success": success, "id": self.id},
        )


@contextmanager
def job_run(conn, workflow: str) -> Iterator[JobRun]:
    run = JobRun(conn, workflow)
    try:
        yield run
    except BaseException:
        run.finish(False)
        raise
    run.finish(True)


__all__ = ["connect", "get_config", "set_config", "log_error", "log_exception", "job_run", "JobRun", "Jsonb"]

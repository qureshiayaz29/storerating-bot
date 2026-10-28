"""Database fixture: a throwaway database with every migration applied.

Set TEST_DATABASE_URL to a Postgres server URL (any database on it). Tests
that need the database are skipped when it is not set.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("deliveries", "alerts", "errors", "job_runs", "events", "fn_metrics", "platform_usage", "daily_stats", "spend",
          "on_demand", "cards", "summaries", "app_cursor", "app_snapshots", "watchlist", "apps", "users", "config")


@pytest.fixture(scope="session")
def db_url():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL not set")
    name = f"srtest_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
        for role in ("anon", "authenticated"):
            admin.execute(f"do $$ begin create role {role} nologin; exception when duplicate_object then null; end $$")
    url = psycopg.conninfo.make_conninfo(base, dbname=name)
    with psycopg.connect(url, autocommit=True) as conn:
        for f in sorted((ROOT / "db").glob("*.sql")):
            conn.execute(f.read_text())
        conn.execute("create table _config_seed as select * from config")
    yield url
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'drop database "{name}" with (force)')


@pytest.fixture
def conn(db_url):
    from job.db import connect

    c = connect(db_url)
    c.execute(f"truncate {', '.join(TABLES)} restart identity cascade")
    c.execute("insert into config select * from _config_seed")
    yield c
    c.close()

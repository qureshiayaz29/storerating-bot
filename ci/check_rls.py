"""Fail when any table in schema public has Row Level Security disabled.

Usage: DATABASE_URL=... python ci/check_rls.py
Used in CI against a throwaway Postgres with all migrations applied, and can be
run against Supabase with the App Role connection string.
"""

from __future__ import annotations

import os
import sys

import psycopg

QUERY = """
select c.relname
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity
order by 1
"""


def main() -> int:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = [r[0] for r in conn.execute(QUERY).fetchall()]
    if rows:
        print("Tables without Row Level Security: " + ", ".join(rows))
        return 1
    print("OK: every public table has Row Level Security enabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())

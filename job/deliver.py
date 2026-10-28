"""Deliver Daily Updates to every User whose Digest Time has arrived.

python -m job.deliver — runs every 30 minutes from .github/workflows/deliver.yml.
Cheap when there is nothing to send: one query, no Telegram calls.
"""

from __future__ import annotations

import os

from .db import connect, job_run
from .send import Pacer, due_updates
from .telegram import Telegram


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set: skipping (secrets not configured yet)")
        return
    conn = connect()
    items = due_updates(conn)
    if not items:
        print("Nothing due")
        return
    with job_run(conn, "deliver") as run:
        stats = Pacer(conn, Telegram(), run).send_all(items)
    print(f"Delivered to {len({i.chat_id for i in items})} users: {stats}")


if __name__ == "__main__":
    main()

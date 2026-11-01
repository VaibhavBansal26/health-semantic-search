"""Run every connector on a schedule, one at a time, without ever running two syncs at once.

    uv run python -m hss.sync.runner --once                  # one pass (put this in cron or launchd)
    uv run python -m hss.sync.runner --every-hours 6         # or let it loop

A Postgres advisory lock stops a second runner from starting while one is working, so a slow pass can not be doubled up
by the next scheduled start. One source failing is logged and does not stop the others; its cursor has not moved, so the
next pass resumes where it stopped. Prune is slower than a sync and runs only with --prune-weekly (Sundays).
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime, timedelta

import psycopg

from hss.sync import ctgov, openfda
from hss.sync.core import ensure_schema, prune_missing, run_sync

LOCK_KEY = 8_117_402  # arbitrary, shared by every runner of this project
DEFAULT_DSN = "postgresql://hss:hss_dev_only@localhost:5440/hss"
FIRST_RUN_DAYS = 14  # a source with no cursor starts this many days back


def one_pass(dsn: str, prune: bool) -> dict[str, str]:
    results: dict[str, str] = {}
    with psycopg.connect(dsn) as conn, psycopg.connect(dsn) as lookup:
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            return {"skipped": "another sync is running"}
        try:
            ensure_schema(conn)
            since = (date.today() - timedelta(days=FIRST_RUN_DAYS)).isoformat()

            def have(set_id: str) -> bool:
                return lookup.execute("SELECT 1 FROM documents WHERE source = 'dailymed' AND set_id = %s", (set_id,)).fetchone() is not None

            jobs = {
                ctgov.SOURCE: (ctgov.make_fetch(since), ctgov.present_ids),
                openfda.SOURCE: (openfda.make_fetch(since, have), openfda.present_ids),
            }
            for name, (fetch, present) in jobs.items():
                try:
                    counts = run_sync(conn, name, fetch)
                    note = str(counts)
                    if prune:
                        note += f", pruned {prune_missing(conn, name, present)}"
                    results[name] = note
                except Exception as exc:  # noqa: BLE001 - one source failing must not stop the next one
                    conn.rollback()
                    results[name] = f"FAILED: {type(exc).__name__}: {exc}"[:300]
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            conn.commit()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--every-hours", type=float)
    parser.add_argument("--prune-weekly", action="store_true")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()
    if not args.once and not args.every_hours:
        parser.error("pass --once or --every-hours N")
    while True:
        prune = args.prune_weekly and datetime.now().weekday() == 6
        print(datetime.now().isoformat(timespec="seconds"), one_pass(args.dsn, prune), flush=True)
        if args.once:
            return
        time.sleep(args.every_hours * 3600)


if __name__ == "__main__":
    main()

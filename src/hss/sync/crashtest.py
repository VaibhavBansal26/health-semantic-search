"""Kill the sync mid-run, over and over, and check that nothing is missing or duplicated.

    uv run python -m hss.sync.crashtest

Uses a fake source ('crashtest') so the result is deterministic. Scenarios, each ending in a clean run:
  1. hard exits (os._exit, no cleanup) at fixed points inside a page's transaction
  2. SIGKILL from outside at random times while the sync is writing
  3. the same, after some records were revised at the source (their date moves forward)
Checks against what the source holds: same set of ids, one row per id, content hash and chunk count per document equal
to a clean load, no orphan sections or chunks, cursor at the newest date.
"""

from __future__ import annotations

import os
import random
import signal
import subprocess
import sys
import time
from datetime import date, timedelta

import psycopg

from hss.ingest.chunker import chunk_section
from hss.sync.core import Record, ensure_schema, get_cursor, run_sync

SOURCE = "crashtest"
DSN = os.environ.get("DATABASE_URL", "postgresql://hss:hss_dev_only@localhost:5440/hss")
N = 300
PAGE = 25
START = date(2026, 1, 1)


def make_records(revision: int) -> list[Record]:
    """300 records, ~10 per day over 30 days. Revision 1 rewrites 40 of them and moves their date forward."""
    recs = []
    for i in range(N):
        updated = START + timedelta(days=i // 10)
        body = f"Study {i}. " + ("The participants receive the drug twice daily and are followed for six months. " * (3 + i % 40))
        if revision and i % 7 == 0:
            body += f" Amended in revision {revision}."
            updated = START + timedelta(days=30 + i // 40)
        recs.append(Record(f"CT{i:05d}", f"Trial {i}", (("Brief Summary", body), ("Eligibility Criteria", f"Adults aged {18 + i % 40} and over.")), updated))
    return recs


def fetch_from(records: list[Record], delay: float = 0.0):
    def fetch(cursor: str | None):
        rows = sorted((r for r in records if cursor is None or r.updated.isoformat() >= cursor), key=lambda r: (r.updated, r.doc_id))
        for i in range(0, len(rows), PAGE):
            yield rows[i : i + PAGE]
            time.sleep(delay)

    return fetch


def child(revision: int, crash_after: int | None, delay: float) -> None:
    def hook(n: int) -> None:
        if delay:
            time.sleep(delay)
        if crash_after is not None and n == crash_after:
            os._exit(137)  # dies inside the open transaction, no rollback call, no cleanup

    with psycopg.connect(DSN) as conn:
        run_sync(conn, SOURCE, fetch_from(make_records(revision)), on_record=hook)


def spawn(revision: int, crash_after: int | None = None, delay: float = 0.0) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "hss.sync.crashtest", "child", str(revision), str(crash_after or ""), str(delay)])


def verify(revision: int) -> list[str]:
    expected = {r.doc_id: r for r in make_records(revision)}
    problems = []
    with psycopg.connect(DSN) as conn:
        rows = conn.execute("SELECT set_id, content_hash, id FROM documents WHERE source = %s", (SOURCE,)).fetchall()
        ids = [r[0] for r in rows]
        if len(ids) != len(set(ids)):
            problems.append("duplicate documents")
        if set(ids) != set(expected):
            problems.append(f"missing {sorted(set(expected) - set(ids))[:5]} extra {sorted(set(ids) - set(expected))[:5]}")
        for set_id, h, doc_pk in rows:
            rec = expected.get(set_id)
            if rec and h != rec.content_hash:
                problems.append(f"stale content for {set_id}")
            n_chunks = conn.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (doc_pk,)).fetchone()[0]
            if rec and n_chunks != sum(len(chunk_section(t)) for _, t in rec.sections):
                problems.append(f"chunk count wrong for {set_id}")
        orphans = conn.execute(
            "SELECT (SELECT count(*) FROM sections s LEFT JOIN documents d ON d.id = s.document_id WHERE d.id IS NULL), "
            "(SELECT count(*) FROM chunks c LEFT JOIN sections s ON s.id = c.section_id WHERE s.id IS NULL)"
        ).fetchone()
        if any(orphans):
            problems.append(f"orphans {orphans}")
        newest = max(r.updated for r in expected.values()).isoformat()
        if get_cursor(conn, SOURCE) != newest:
            problems.append(f"cursor {get_cursor(conn, SOURCE)} != {newest}")
    return problems


def reset() -> None:
    with psycopg.connect(DSN) as conn:
        ensure_schema(conn)
        conn.execute("DELETE FROM documents WHERE source = %s", (SOURCE,))
        conn.execute("DELETE FROM sync_state WHERE source = %s", (SOURCE,))
        conn.commit()


def finish(revision: int) -> None:
    assert spawn(revision).wait() == 0, "clean run failed"


def scenario_exits() -> list[str]:
    reset()
    crashes = 0
    for point in (3, 24, 25, 26, 60, 137, 50):  # inside a page, on a page edge, just after one
        crashes += spawn(0, point).wait() == 137
    finish(0)
    print(f"  exits: {crashes} hard exits, then a clean run")
    return verify(0)


def scenario_sigkill(revision: int, kills: int, rng: random.Random) -> list[str]:
    done = 0
    for _ in range(kills):
        proc = spawn(revision, delay=0.01)
        time.sleep(rng.uniform(0.2, 1.8))
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            done += 1
        proc.wait()
    finish(revision)
    print(f"  revision {revision}: {done} SIGKILLs, then a clean run")
    return verify(revision)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        child(int(sys.argv[2]), int(sys.argv[3]) if sys.argv[3] else None, float(sys.argv[4]))
        return
    rng = random.Random(7)
    results = {"hard exits": scenario_exits()}
    reset()
    results["SIGKILL"] = scenario_sigkill(0, 8, rng)
    results["SIGKILL after revisions"] = scenario_sigkill(1, 8, rng)
    reset()
    for name, problems in results.items():
        print(f"{name:26s} {'OK' if not problems else problems}")
    sys.exit(any(results.values()))


if __name__ == "__main__":
    main()

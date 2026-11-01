"""Incremental sync that survives being killed.

A source yields pages of records ordered by their update date, starting at a cursor (a date). For each page we write
the changed documents and move the cursor in ONE transaction, so after a crash the database holds either all of a page
and the cursor past it, or none of it and the cursor where it was. The next run restarts at the cursor, and the range
is inclusive, so the cursor's own day is fetched again: no record can be missed, and a record fetched twice is skipped
because its content hash is unchanged.

  No gaps        a page and its cursor commit together; restarts re-read from the cursor day, never after it
  No duplicates  UNIQUE (source, set_id) in the schema, and replacement is delete + insert in one transaction
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from hss.ingest.chunker import approx_tokens, chunk_section

SCHEMA = Path(__file__).resolve().parents[3] / "sql" / "002_sync.sql"


@dataclass(frozen=True)
class Record:
    doc_id: str
    title: str
    sections: tuple[tuple[str, str], ...]  # (section title, text)
    updated: date
    doc_type: str = ""

    @property
    def content_hash(self) -> str:
        payload = json.dumps([self.title, self.doc_type, self.sections], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA.read_text())
    conn.commit()


def get_cursor(conn: psycopg.Connection, source: str) -> str | None:
    row = conn.execute("SELECT cursor FROM sync_state WHERE source = %s", (source,)).fetchone()
    return row[0] if row else None


def upsert_record(conn: psycopg.Connection, source: str, rec: Record) -> str:
    """Write one record. Returns 'loaded', 'updated' or 'unchanged'."""
    row = conn.execute("SELECT id, content_hash FROM documents WHERE source = %s AND set_id = %s", (source, rec.doc_id)).fetchone()
    status = "loaded"
    if row:
        if row[1] == rec.content_hash:
            return "unchanged"
        conn.execute("DELETE FROM documents WHERE id = %s", (row[0],))  # cascades to sections and chunks
        status = "updated"
    doc_id = conn.execute(
        """INSERT INTO documents (source, set_id, version, doc_type, title, effective_date, content_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (source, rec.doc_id, int(rec.updated.strftime("%Y%m%d")), rec.doc_type, rec.title, rec.updated, rec.content_hash),
    ).fetchone()[0]
    for ordinal, (title, text) in enumerate(rec.sections):
        section_id = conn.execute(
            "INSERT INTO sections (document_id, ordinal, title, text) VALUES (%s, %s, %s, %s) RETURNING id",
            (doc_id, ordinal, title, text),
        ).fetchone()[0]
        for index, piece in enumerate(chunk_section(text)):
            conn.execute(
                "INSERT INTO chunks (document_id, section_id, chunk_index, text, approx_tokens) VALUES (%s, %s, %s, %s, %s)",
                (doc_id, section_id, index, piece, approx_tokens(piece)),
            )
    return status


def run_sync(
    conn: psycopg.Connection,
    source: str,
    fetch: Callable[[str | None], Iterable[list[Record]]],
    on_record: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Pull every page after the stored cursor. `on_record(n)` runs after each write, inside the transaction (a test hook)."""
    counts = {"loaded": 0, "updated": 0, "unchanged": 0}
    written = 0
    for page in fetch(get_cursor(conn, source)):
        if not page:
            continue
        with conn.transaction():
            for rec in page:
                counts[upsert_record(conn, source, rec)] += 1
                written += 1
                if on_record:
                    on_record(written)
            newest = max(r.updated for r in page).isoformat()
            conn.execute(
                """INSERT INTO sync_state (source, cursor) VALUES (%s, %s)
                   ON CONFLICT (source) DO UPDATE SET cursor = GREATEST(sync_state.cursor, EXCLUDED.cursor), updated_at = now()""",
                (source, newest),
            )
    return counts


class SuspiciousPrune(RuntimeError):
    pass


def prune_missing(
    conn: psycopg.Connection,
    source: str,
    present: Callable[[list[str]], set[str]],
    batch: int = 100,
    max_missing_share: float = 0.2,
) -> int:
    """Delete stored documents the source no longer returns. `present(ids)` must return the subset of `ids` the source
    still serves, and raise if it cannot tell (an error must never read as "everything is gone").

    Safety: if more than `max_missing_share` of a batch looks missing, the source is more likely broken than edited, so
    nothing is deleted and SuspiciousPrune is raised. Each batch deletes in its own transaction.
    """
    ids = [r[0] for r in conn.execute("SELECT set_id FROM documents WHERE source = %s ORDER BY set_id", (source,)).fetchall()]
    removed = 0
    for i in range(0, len(ids), batch):
        chunk = ids[i : i + batch]
        gone = [s for s in chunk if s not in present(chunk)]
        if len(gone) > max_missing_share * len(chunk):
            raise SuspiciousPrune(f"{len(gone)} of {len(chunk)} ids look missing in one batch; refusing to delete")
        if gone:
            with conn.transaction():
                conn.execute("DELETE FROM documents WHERE source = %s AND set_id = ANY(%s)", (source, gone))
            removed += len(gone)
    return removed

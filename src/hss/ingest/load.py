"""Parse downloaded labels, chunk them, and load documents/sections/chunks into Postgres.

Re-running is safe: a label is keyed on (source, set_id). If the stored version is
the same or newer it is skipped; if the file holds a newer version, the old rows are
deleted (cascading to sections and chunks) and the label is loaded again.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg

from hss.ingest.chunker import approx_tokens, chunk_section
from hss.ingest.spl_parser import parse_label

DEFAULT_DSN = "postgresql://hss:hss_dev_only@localhost:5440/hss"


def load_label(conn: psycopg.Connection, path: Path) -> str:
    """Load one label file. Returns 'loaded', 'updated', 'skipped' or 'empty'."""
    label = parse_label(path)
    if not label.sections:
        return "empty"

    row = conn.execute(
        "SELECT id, version FROM documents WHERE source = 'dailymed' AND set_id = %s",
        (label.set_id,),
    ).fetchone()
    status = "loaded"
    if row:
        if row[1] >= label.version:
            return "skipped"
        conn.execute("DELETE FROM documents WHERE id = %s", (row[0],))
        status = "updated"

    doc_id = conn.execute(
        """INSERT INTO documents (set_id, version, doc_type, title, labeler, effective_date)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (label.set_id, label.version, label.doc_type, label.title, label.labeler, label.effective_date),
    ).fetchone()[0]

    for section in label.sections:
        section_id = conn.execute(
            """INSERT INTO sections (document_id, ordinal, loinc_code, title, text)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (doc_id, section.ordinal, section.loinc_code, section.title, section.text),
        ).fetchone()[0]
        for index, text in enumerate(chunk_section(section.text)):
            conn.execute(
                """INSERT INTO chunks (document_id, section_id, chunk_index, text, approx_tokens)
                   VALUES (%s, %s, %s, %s, %s)""",
                (doc_id, section_id, index, text, approx_tokens(text)),
            )
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("data/raw/dailymed"))
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()

    counts: dict[str, int] = {}
    files = sorted(args.dir.glob("*.xml"))
    with psycopg.connect(args.dsn) as conn:
        for path in files:
            try:
                with conn.transaction():  # one label = one transaction, a bad file loads nothing
                    status = load_label(conn, path)
            except Exception as exc:  # noqa: BLE001 - report and continue with the next label
                status = "failed"
                print(f"failed {path.name}: {exc}")
            counts[status] = counts.get(status, 0) + 1
    print(f"{len(files)} files:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()

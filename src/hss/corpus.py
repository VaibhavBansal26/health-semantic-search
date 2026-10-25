"""Load the chunk corpus from Postgres into memory, in a fixed order.

Every retriever (BM25, dense, hybrid) indexes this same list, so position i means
the same chunk everywhere. The stable key (set_id, section ordinal, chunk index)
survives a database reload, which is why the golden set stores that and not row ids.
"""

from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DSN = "postgresql://hss:hss_dev_only@localhost:5440/hss"


@dataclass(frozen=True)
class Chunk:
    pos: int
    key: tuple[str, int, int]  # (set_id, section ordinal, chunk index)
    doc_title: str
    section_title: str
    loinc_code: str | None
    text: str

    @property
    def section_key(self) -> tuple[str, int]:
        return self.key[:2]

    @property
    def embed_text(self) -> str:
        """Chunk text with its drug and section name in front, so a short chunk still says what it is about."""
        return f"{self.doc_title} - {self.section_title}\n{self.text}"


def save_chunks(chunks: list[Chunk], path: Path) -> None:
    """Write the corpus as gzipped JSON lines: the database-free copy the deployed demo reads."""
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps({"key": list(c.key), "doc_title": c.doc_title, "section_title": c.section_title,
                                "loinc_code": c.loinc_code, "text": c.text}) + "\n")


def load_chunks_file(path: Path) -> list[Chunk]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return [
        Chunk(pos=i, key=tuple(r["key"]), doc_title=r["doc_title"], section_title=r["section_title"],
              loinc_code=r["loinc_code"], text=r["text"])
        for i, r in enumerate(rows)
    ]


def load_chunks(dsn: str | None = None) -> list[Chunk]:
    import psycopg  # imported here so the deployed demo does not need a database driver

    dsn = dsn or os.environ.get("DATABASE_URL", DEFAULT_DSN)
    sql = """
        SELECT d.set_id, s.ordinal, c.chunk_index, d.title, s.title, s.loinc_code, c.text
        FROM chunks c
        JOIN sections s  ON s.id = c.section_id
        JOIN documents d ON d.id = c.document_id
        ORDER BY d.set_id, s.ordinal, c.chunk_index
    """
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(sql).fetchall()
    return [
        Chunk(pos=i, key=(r[0], r[1], r[2]), doc_title=r[3], section_title=r[4], loinc_code=r[5], text=r[6])
        for i, r in enumerate(rows)
    ]

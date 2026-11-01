"""Keep the search index in step with the database after a sync.

    uv run python -m hss.reindex --root data/live --seed deploy/space/data --aliases data/aliases.json

An index version is a directory (corpus.jsonl.gz, embeddings.npy, meta.json). `CURRENT` names the live one and is
replaced atomically, so a reader sees the old version or the new one, never a corpus from one and vectors from the other.

Only chunks whose embedded text is new get embedded; every other vector is copied from the previous version. A signature
of the documents (source, set_id, version, content hash) is stored with each version, so a pass that finds the same
signature does nothing, and a pass that crashed after the sync but before the index was written is repaired by the next
one (a changed-count check would miss that).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from hss.aliases import with_aliases
from hss.corpus import Chunk, DEFAULT_DSN, load_chunks, load_chunks_file, save_chunks

MODEL = "BAAI/bge-small-en-v1.5"
SOURCES = ("dailymed", "openfda")  # sources the search covers; trials stay out (no measured gain, see the log)
KEEP_VERSIONS = 2

Embedder = Callable[[list[str]], np.ndarray]


def signature(conn, sources: tuple[str, ...]) -> str:
    rows = conn.execute(
        "SELECT source, set_id, version, COALESCE(content_hash, '') FROM documents WHERE source = ANY(%s) ORDER BY source, set_id",
        (list(sources),),
    ).fetchall()
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def current(root: Path) -> Path | None:
    pointer = root / "CURRENT"
    return root / pointer.read_text().strip() if pointer.exists() else None


def read_meta(version: Path | None) -> dict:
    return json.loads((version / "meta.json").read_text()) if version and (version / "meta.json").exists() else {}


def build(chunks: list[Chunk], old_chunks: list[Chunk], old_vectors: np.ndarray, embed: Embedder) -> tuple[np.ndarray, int, int]:
    """Vectors for `chunks`: reuse a previous vector when the text to embed is identical, embed the rest."""
    known: dict[str, int] = {}
    for i, c in enumerate(old_chunks):
        known.setdefault(c.embed_text, i)
    dim = old_vectors.shape[1] if len(old_vectors) else None
    missing = [i for i, c in enumerate(chunks) if c.embed_text not in known]
    fresh = embed([chunks[i].embed_text for i in missing]).astype(np.float32) if missing else np.zeros((0, dim or 0), np.float32)
    dim = dim or fresh.shape[1]
    vectors = np.zeros((len(chunks), dim), np.float32)
    for i, c in enumerate(chunks):
        if c.embed_text in known:
            vectors[i] = old_vectors[known[c.embed_text]]
    vectors[missing] = fresh
    return vectors, len(chunks) - len(missing), len(missing)


def publish(root: Path, chunks: list[Chunk], vectors: np.ndarray, meta: dict) -> Path:
    assert len(chunks) == len(vectors), "corpus and vectors out of step"
    name = f"v{int(time.time() * 1000)}"
    version = root / name
    version.mkdir(parents=True)
    save_chunks(chunks, version / "corpus.jsonl.gz")
    np.save(version / "embeddings.npy", vectors)
    (version / "meta.json").write_text(json.dumps(meta))
    tmp = root / "CURRENT.tmp"
    tmp.write_text(name)
    os.replace(tmp, root / "CURRENT")  # the one atomic step: readers switch from the old version to this one
    for old in sorted(p for p in root.glob("v*") if p.is_dir())[:-KEEP_VERSIONS]:
        shutil.rmtree(old, ignore_errors=True)
    return version


def seed_from(seed: Path | None) -> tuple[list[Chunk], np.ndarray]:
    """Start from an existing corpus and vectors (the deployed demo's) so the first pass does not embed everything."""
    if seed and (seed / "corpus.jsonl.gz").exists():
        file = seed / "embeddings.npy"
        file = file if file.exists() else seed / "embeddings_fp16.npy"
        return load_chunks_file(seed / "corpus.jsonl.gz"), np.load(file).astype(np.float32)
    return [], np.zeros((0, 0), np.float32)


def make_embedder() -> Embedder:
    from sentence_transformers import SentenceTransformer

    from hss.retrievers import pick_device

    model = SentenceTransformer(MODEL, device=pick_device())
    model.max_seq_length = 512
    return lambda texts: model.encode(texts, batch_size=64, normalize_embeddings=True)


def refresh(
    root: Path,
    dsn: str = DEFAULT_DSN,
    sources: tuple[str, ...] = SOURCES,
    aliases: dict | None = None,
    seed: Path | None = None,
    embed: Embedder | None = None,
) -> dict:
    """Bring the live index up to date with the database. Returns what it did; a no-op returns {'changed': False}."""
    import psycopg

    root.mkdir(parents=True, exist_ok=True)
    live = current(root)
    with psycopg.connect(dsn) as conn:
        sig = signature(conn, sources)
    if read_meta(live).get("signature") == sig:
        return {"changed": False}
    chunks = [c for s in sources for c in load_chunks(dsn, s)]
    if aliases:
        chunks = with_aliases(chunks, aliases)
    chunks = [dataclasses.replace(c, pos=i) for i, c in enumerate(chunks)]
    if live:
        old_chunks, old_vectors = load_chunks_file(live / "corpus.jsonl.gz"), np.load(live / "embeddings.npy")
    else:
        old_chunks, old_vectors = seed_from(seed)
    needs = {c.embed_text for c in chunks} - {c.embed_text for c in old_chunks}
    vectors, reused, embedded = build(chunks, old_chunks, old_vectors, embed or (make_embedder() if needs else (lambda t: np.zeros((0, 0)))))
    version = publish(root, chunks, vectors, {"signature": sig, "chunks": len(chunks), "reused": reused, "embedded": embedded})
    return {"changed": True, "version": version.name, "chunks": len(chunks), "reused": reused, "embedded": embedded}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/live"))
    parser.add_argument("--seed", type=Path, help="directory with corpus.jsonl.gz and embeddings(_fp16).npy to reuse vectors from")
    parser.add_argument("--aliases", type=Path)
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()
    aliases = json.loads(args.aliases.read_text()) if args.aliases else None
    print(refresh(args.root, args.dsn, aliases=aliases, seed=args.seed))


if __name__ == "__main__":
    main()

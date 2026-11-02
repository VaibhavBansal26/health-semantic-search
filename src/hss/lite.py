"""A search engine that fits in 512 MB: the same BM25, dense and hybrid retrievers, without the reranker or cited answers.

    uv run python -m hss.lite build --corpus data/corpus.jsonl.gz --embeddings data/embeddings_fp16.npy --out data/lite
    LiteEngine(Path("data/lite"))

Where the memory goes in the full engine, and what this one does instead:
  chunk text (about 150 MB as Python strings)   kept in SQLite on disk; only the few chunks in a result are read
  BM25 index built at start (peak above 800 MB) built once by `build`, loaded memory-mapped
  float32 embedding matrix (80 MB)              kept as float16 on disk, memory-mapped, multiplied in blocks
  PyTorch and sentence-transformers             ONNX Runtime through fastembed, same BAAI/bge-small-en-v1.5 weights
  cross-encoder reranker (about 1 GB)           left out, so there are no cited answers in this build

Chunk metadata (ids, drug and section names) stays in memory because the access mask is computed from it. The vectors
match the sentence-transformers ones to cosine 0.9999 on the chunks checked, so the stored document embeddings are reused.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import threading
from pathlib import Path

import bm25s
import numpy as np
import Stemmer

from hss.acl import Principal, allowed_mask, doc_groups
from hss.cache import CachedSearch
from hss.corpus import Chunk, load_chunks_file
from hss.retrievers import BGE_QUERY_PROMPT, AdaptiveHybridRetriever, is_code_like_wide

MODEL = "BAAI/bge-small-en-v1.5"
BLOCK = 8192  # rows multiplied at a time, so a query never holds more than a block of float32 vectors


def build(corpus: Path, embeddings: Path, out: Path) -> dict:
    """Write the on-disk artifacts: chunk text (SQLite), chunk metadata, a saved BM25 index and float16 vectors."""
    from hss.retrievers import BM25Retriever

    out.mkdir(parents=True, exist_ok=True)
    chunks = load_chunks_file(corpus)
    vectors = np.load(embeddings)
    if len(vectors) != len(chunks):
        raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
    db = out / "texts.sqlite"
    db.unlink(missing_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chunks (pos INTEGER PRIMARY KEY, text TEXT NOT NULL)")
    conn.executemany("INSERT INTO chunks VALUES (?, ?)", ((c.pos, c.text) for c in chunks))
    conn.commit()
    conn.close()
    with gzip.open(out / "meta.json.gz", "wt", encoding="utf-8") as f:
        json.dump([[*c.key, c.doc_title, c.section_title, c.loinc_code] for c in chunks], f)
    BM25Retriever(chunks, with_header=True).index.save(str(out / "bm25"))
    np.save(out / "embeddings_fp16.npy", vectors.astype(np.float16))
    info = {"chunks": len(chunks), "model": MODEL, "dim": int(vectors.shape[1])}
    (out / "lite.json").write_text(json.dumps(info))
    return info


class LiteBM25:
    def __init__(self, directory: Path):
        self.stemmer = Stemmer.Stemmer("english")
        self.index = bm25s.BM25.load(str(directory), mmap=True)

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        q = bm25s.tokenize(query, stopwords="en", stemmer=self.stemmer, show_progress=False)
        if allowed is None:
            docs, _ = self.index.retrieve(q, k=k, show_progress=False)
            return [int(i) for i in docs[0]]
        docs, _ = self.index.retrieve(q, k=k, show_progress=False, weight_mask=allowed.astype(np.float32))
        return [int(i) for i in docs[0] if allowed[i]]


class LiteDense:
    def __init__(self, vectors: Path, model_name: str = MODEL, embed=None):
        self.matrix = np.load(vectors, mmap_mode="r")
        self._embed = embed
        self.model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _encode(self, query: str) -> np.ndarray:
        if self._embed is not None:  # injected in tests
            vec = self._embed(query)
        else:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(self.model_name)
                vec = next(iter(self._model.embed([BGE_QUERY_PROMPT + query])))
        vec = np.asarray(vec, dtype=np.float32)
        return vec / np.linalg.norm(vec)

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        q = self._encode(query)
        scores = np.empty(len(self.matrix), dtype=np.float32)
        for start in range(0, len(self.matrix), BLOCK):
            scores[start : start + BLOCK] = self.matrix[start : start + BLOCK].astype(np.float32) @ q
        if allowed is not None:
            scores = np.where(allowed, scores, -np.inf)
            k = min(k, int(allowed.sum()))
            if k == 0:
                return []
        top = np.argpartition(-scores, k - 1)[:k]
        return [int(i) for i in top[np.argsort(-scores[top])]]


class LiteEngine:
    """Implements the `hss.api.Engine` protocol for keyword, dense and hybrid search. `answer` is not available."""

    modes = ("keyword", "dense", "hybrid")
    answers = False
    name = "lite"

    def __init__(self, root: Path, tenants: dict[str, str] | None = None, section_rules: dict | None = None, embed=None):
        self.tenants, self.section_rules = tenants, section_rules
        rows = json.loads(gzip.open(root / "meta.json.gz", "rt", encoding="utf-8").read())
        self.chunks = [
            Chunk(pos=i, key=(r[0], r[1], r[2]), doc_title=r[3], section_title=r[4], loinc_code=r[5], text="")
            for i, r in enumerate(rows)
        ]
        self.acls = doc_groups(self.chunks)
        self._texts = sqlite3.connect(str(root / "texts.sqlite"), check_same_thread=False)
        self._text_lock = threading.Lock()
        bm25, dense = LiteBM25(root / "bm25"), LiteDense(root / "embeddings_fp16.npy", embed=embed)
        hybrid = AdaptiveHybridRetriever(bm25, dense, skip_dense=True, detector=is_code_like_wide)
        self.caches = {"keyword": CachedSearch(bm25), "dense": CachedSearch(dense), "hybrid": CachedSearch(hybrid)}
        self._masks: dict[tuple, np.ndarray] = {}

    def mask_for(self, who: Principal) -> np.ndarray:
        key = (who.admin, who.tenant, who.groups)
        if key not in self._masks:
            self._masks[key] = allowed_mask(self.chunks, self.acls, who, self.tenants, self.section_rules)
        return self._masks[key]

    def _text(self, pos: int) -> str:
        with self._text_lock:
            row = self._texts.execute("SELECT text FROM chunks WHERE pos = ?", (pos,)).fetchone()
        return row[0] if row else ""

    def search(self, mode: str, query: str, k: int, mask: np.ndarray) -> list[dict]:
        return [
            {"label": c.key[0], "drug": c.doc_title, "section": c.section_title, "text": self._text(pos)}
            for pos in self.caches[mode].search(query, k, allowed=mask)
            for c in [self.chunks[pos]]
        ]

    def answer(self, query: str, mask: np.ndarray) -> dict:
        raise NotImplementedError("cited answers need the reranker, which this build leaves out")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--corpus", type=Path, required=True)
    b.add_argument("--embeddings", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.corpus, args.embeddings, args.out))


if __name__ == "__main__":
    main()

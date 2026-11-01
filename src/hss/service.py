"""The real engine behind the API: BM25, dense, routed hybrid and reranker over one corpus, with access masks.

Builds the same retrievers the demo uses. Access masks are computed once per distinct (groups, tenant, admin) and reused.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path

import numpy as np

from hss.acl import Principal, allowed_mask, doc_groups
from hss.answer import Answerer
from hss.cache import CachedSearch, IndexChanged
from hss.corpus import load_chunks_file
from hss.retrievers import AdaptiveHybridRetriever, BM25Retriever, DenseRetriever, Reranker, is_code_like_wide

REFUSAL_THRESHOLD = 0.87
MODEL = "BAAI/bge-small-en-v1.5"


class HssEngine:
    modes = ("keyword", "dense", "hybrid", "rerank")

    def __init__(self, corpus: Path, embeddings: Path, tenants: dict[str, str] | None = None, section_rules: dict | None = None, live_root: Path | None = None):
        self.tenants, self.section_rules, self.live_root = tenants, section_rules, live_root
        self._swap = threading.Lock()
        self._loaded: str | None = None
        self.chunks = load_chunks_file(corpus)
        self.acls = doc_groups(self.chunks)
        bm25 = BM25Retriever(self.chunks, with_header=True)
        dense = DenseRetriever(np.load(embeddings).astype(np.float32), MODEL)
        hybrid = AdaptiveHybridRetriever(bm25, dense, skip_dense=True, detector=is_code_like_wide)
        self.reranker = Reranker(hybrid, self.chunks)
        self._wire({"keyword": bm25, "dense": dense, "hybrid": hybrid, "rerank": self.reranker})
        self._masks: dict[tuple, np.ndarray] = {}
        if live_root:
            self._loaded = (live_root / "CURRENT").read_text().strip() if (live_root / "CURRENT").exists() else None

    def _wire(self, by_mode: dict) -> None:
        self.by_mode = by_mode
        self.caches = {m: CachedSearch(r) for m, r in by_mode.items()}

    def maybe_reload(self) -> bool:
        """Switch to a newer index version if the sync runner published one. Models stay loaded; only data is rebuilt."""
        if not self.live_root or not (self.live_root / "CURRENT").exists():
            return False
        name = (self.live_root / "CURRENT").read_text().strip()
        if name == self._loaded:
            return False
        with self._swap:
            if name == self._loaded:
                return False
            version = self.live_root / name
            chunks = load_chunks_file(version / "corpus.jsonl.gz")
            vectors = np.load(version / "embeddings.npy").astype(np.float32)
            bm25 = BM25Retriever(chunks, with_header=True)
            dense = copy.copy(self.by_mode["dense"])  # shares the loaded model
            dense.matrix = vectors
            hybrid = AdaptiveHybridRetriever(bm25, dense, skip_dense=True, detector=is_code_like_wide)
            reranker = copy.copy(self.reranker)
            reranker.first_stage, reranker.chunks = hybrid, chunks
            # Replace everything the request path reads as one step, then drop what was computed against the old index.
            old_caches = self.caches
            self.chunks, self.acls, self.reranker, self._masks = chunks, doc_groups(chunks), reranker, {}
            self._wire({"keyword": bm25, "dense": dense, "hybrid": hybrid, "rerank": reranker})
            for cache, fresh in zip(old_caches.values(), self.caches.values()):
                fresh.version = cache.version + 1
            self._loaded = name
        return True

    def mask_for(self, who: Principal) -> np.ndarray:
        self.maybe_reload()
        key = (who.admin, who.tenant, who.groups)
        if key not in self._masks:
            self._masks[key] = allowed_mask(self.chunks, self.acls, who, self.tenants, self.section_rules)
        return self._masks[key]

    def search(self, mode: str, query: str, k: int, mask: np.ndarray) -> list[dict]:
        if len(mask) != len(self.chunks):
            raise IndexChanged("index updated while the request was running")
        out = []
        for pos in self.caches[mode].search(query, k, allowed=mask):
            c = self.chunks[pos]
            out.append({"label": c.key[0], "drug": c.doc_title, "section": c.section_title, "text": c.text})
        return out

    def answer(self, query: str, mask: np.ndarray) -> dict:
        if len(mask) != len(self.chunks):
            raise IndexChanged("index updated while the request was running")
        result = Answerer(self.reranker, self.chunks, REFUSAL_THRESHOLD).answer(query, allowed=mask)
        return {"supported": result.supported, "top_score": result.top_score,
                "citations": [{"sentence": c.sentence, "drug": c.drug, "section": c.section, "label": c.set_id} for c in result.citations]}


def main() -> None:
    import argparse
    import os

    import uvicorn

    from hss.api import create_app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("deploy/space/data/corpus.jsonl.gz"))
    parser.add_argument("--embeddings", type=Path, default=Path("deploy/space/data/embeddings_fp16.npy"))
    parser.add_argument("--live-root", type=Path, help="index directory the sync runner publishes to; newer versions are picked up")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    key = os.environ.get("HSS_JWT_KEY")
    if not key:
        raise SystemExit("set HSS_JWT_KEY (the secret tokens are signed with)")
    uvicorn.run(create_app(HssEngine(args.corpus, args.embeddings, live_root=args.live_root), key), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

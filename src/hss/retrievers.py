"""The retrievers we compare. Each one answers search(query, k) -> chunk positions, best first.

  BM25Retriever     keyword scoring (term frequency, rarity of the term, length normalisation)
  DenseRetriever    meaning match: embed the query, rank chunks by cosine similarity
  rrf               fuse several ranked lists into one (Reciprocal Rank Fusion)
  Reranker          a cross-encoder that reads query and chunk together and re-scores a short list
"""

from __future__ import annotations

import re
from pathlib import Path

import bm25s
import numpy as np
import Stemmer

from hss.corpus import Chunk

BGE_QUERY_PROMPT = "Represent this sentence for searching relevant passages: "


def pick_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], with_header: bool = False):
        self.stemmer = Stemmer.Stemmer("english")
        texts = [c.embed_text if with_header else c.text for c in chunks]
        tokens = bm25s.tokenize(texts, stopwords="en", stemmer=self.stemmer, show_progress=False)
        self.index = bm25s.BM25()
        self.index.index(tokens, show_progress=False)

    def search(self, query: str, k: int = 50) -> list[int]:
        q = bm25s.tokenize(query, stopwords="en", stemmer=self.stemmer, show_progress=False)
        docs, _ = self.index.retrieve(q, k=k, show_progress=False)
        return [int(i) for i in docs[0]]


class DenseRetriever:
    def __init__(self, embeddings_path: Path, model_name: str, query_prompt: str = BGE_QUERY_PROMPT):
        from sentence_transformers import SentenceTransformer

        self.matrix = np.load(embeddings_path)  # (n_chunks, dim), rows already L2-normalised
        self.model = SentenceTransformer(model_name, device=pick_device())
        self.query_prompt = query_prompt

    def search(self, query: str, k: int = 50) -> list[int]:
        q = self.model.encode(query, prompt=self.query_prompt, normalize_embeddings=True)
        scores = self.matrix @ q
        top = np.argpartition(-scores, k)[:k]
        return [int(i) for i in top[np.argsort(-scores[top])]]


def rrf(rankings: list[list[int]], k: int = 60, top: int = 50, weights: list[float] | None = None) -> list[int]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of weight / (k + rank). Uses ranks only, so BM25 and cosine scores need no calibration."""
    weights = weights or [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, pos in enumerate(ranking, start=1):
            scores[pos] = scores.get(pos, 0.0) + weight / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)[:top]


class HybridRetriever:
    def __init__(self, parts: list, per_part: int = 50, rrf_k: int = 60, weights: list[float] | None = None):
        self.parts, self.per_part, self.rrf_k, self.weights = parts, per_part, rrf_k, weights

    def search(self, query: str, k: int = 50) -> list[int]:
        return rrf([p.search(query, self.per_part) for p in self.parts], k=self.rrf_k, top=k, weights=self.weights)


# Identifier-looking tokens: digit groups joined by hyphens (NDC, CAS) or letters mixed with digits (imprints like A109)
CODE_LIKE = re.compile(r"\b\d+(?:-\d+)+\b|\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z\d]{3,}\b")


def is_code_like(query: str) -> bool:
    return CODE_LIKE.search(query) is not None


class AdaptiveHybridRetriever:
    """Hybrid of [keyword, dense] where the keyword list gets `code_weight` when the query contains an identifier.

    Dense embeddings carry no signal for codes, so equal-weight fusion lets that list push the right keyword hit down.
    """

    def __init__(self, keyword, dense, code_weight: float = 4.0, per_part: int = 50, rrf_k: int = 60):
        self.keyword, self.dense, self.code_weight, self.per_part, self.rrf_k = keyword, dense, code_weight, per_part, rrf_k

    def search(self, query: str, k: int = 50) -> list[int]:
        weights = [self.code_weight, 1.0] if is_code_like(query) else [1.0, 1.0]
        lists = [self.keyword.search(query, self.per_part), self.dense.search(query, self.per_part)]
        return rrf(lists, k=self.rrf_k, top=k, weights=weights)


class Reranker:
    """Wraps a first-stage retriever; re-scores its top `depth` chunks with a cross-encoder."""

    def __init__(self, first_stage, chunks: list[Chunk], model_name: str = "BAAI/bge-reranker-base", depth: int = 30):
        from sentence_transformers import CrossEncoder

        self.first_stage, self.chunks, self.depth = first_stage, chunks, depth
        self.model = CrossEncoder(model_name, device=pick_device(), max_length=512)

    def search(self, query: str, k: int = 50) -> list[int]:
        candidates = self.first_stage.search(query, self.depth)
        scores = self.model.predict([(query, self.chunks[i].embed_text) for i in candidates], batch_size=16)
        order = np.argsort(-np.asarray(scores))
        reranked = [candidates[i] for i in order]
        return reranked[:k]

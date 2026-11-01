"""The retrievers we compare. Each one answers search(query, k) -> chunk positions, best first.

  BM25Retriever     keyword scoring (term frequency, rarity of the term, length normalisation)
  DenseRetriever    meaning match: embed the query, rank chunks by cosine similarity
  rrf               fuse several ranked lists into one (Reciprocal Rank Fusion)
  Reranker          a cross-encoder that reads query and chunk together and re-scores a short list

Every search takes an optional `allowed` boolean mask over chunk positions (see hss.acl). Chunks outside the mask are
excluded while candidates are being picked, so a restricted user still gets a full result list, and none of them can
reach the fusion, the reranker or the answer.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import bm25s
import numpy as np
import Stemmer

from hss.corpus import Chunk

# One lock for all model inference in the process. Two threads driving the same model on the Apple GPU (MPS) abort the
# whole process (a Metal "command encoder is already encoding" assertion, seen under the load test), and a single GPU
# runs one batch at a time anyway.
MODEL_LOCK = threading.Lock()

BGE_QUERY_PROMPT = "Represent this sentence for searching relevant passages: "


def pick_device() -> str:
    """The Apple GPU when there is one, else CPU. HSS_DEVICE=cpu forces CPU, to measure what a server without a GPU gets."""
    import os

    import torch

    if os.environ.get("HSS_DEVICE"):
        return os.environ["HSS_DEVICE"]
    return "mps" if torch.backends.mps.is_available() else "cpu"


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], with_header: bool = False, aliases: dict[str, list[str]] | None = None):
        self.stemmer = Stemmer.Stemmer("english")
        texts = [c.embed_text if with_header else c.text for c in chunks]
        if aliases:  # also index the generic name(s), so a brand-titled label matches a question that uses the generic
            texts = [
                f"{', '.join(n for n in aliases.get(c.key[0], []) if n not in c.doc_title.lower())}\n{t}"
                for c, t in zip(chunks, texts)
            ]
        tokens = bm25s.tokenize(texts, stopwords="en", stemmer=self.stemmer, show_progress=False)
        self.index = bm25s.BM25()
        self.index.index(tokens, show_progress=False)

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        q = bm25s.tokenize(query, stopwords="en", stemmer=self.stemmer, show_progress=False)
        if allowed is None:
            docs, _ = self.index.retrieve(q, k=k, show_progress=False)
            return [int(i) for i in docs[0]]
        # weight_mask zeroes the score of chunks the caller may not see; zero-score chunks can still pad the list, so drop them
        docs, _ = self.index.retrieve(q, k=k, show_progress=False, weight_mask=allowed.astype(np.float32))
        return [int(i) for i in docs[0] if allowed[i]]


class DenseRetriever:
    def __init__(self, embeddings_path: Path, model_name: str, query_prompt: str = BGE_QUERY_PROMPT):
        from sentence_transformers import SentenceTransformer

        # (n_chunks, dim), rows already L2-normalised; a ready-made array is accepted so sources can be stacked
        self.matrix = embeddings_path if isinstance(embeddings_path, np.ndarray) else np.load(embeddings_path)
        self.model = SentenceTransformer(model_name, device=pick_device())
        self.query_prompt = query_prompt

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        with MODEL_LOCK:
            q = self.model.encode(query, prompt=self.query_prompt, normalize_embeddings=True)
        scores = self.matrix @ q
        if allowed is not None:
            scores = np.where(allowed, scores, -np.inf)
            k = min(k, int(allowed.sum()))
            if k == 0:
                return []
        top = np.argpartition(-scores, k - 1)[:k]
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

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        lists = [p.search(query, self.per_part, allowed=allowed) for p in self.parts]
        return rrf(lists, k=self.rrf_k, top=k, weights=self.weights)


# Identifier-looking tokens: digit groups joined by hyphens (NDC, CAS) or letters mixed with digits (imprints like A109)
CODE_LIKE = re.compile(r"\b\d+(?:-\d+)+\b|\b(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z\d]{3,}\b")


def is_code_like(query: str) -> bool:
    return CODE_LIKE.search(query) is not None


# Wider detector. Adds what the first one missed on the identifier questions: imprints that are plain numbers ("337") or
# plain words ("CEP"). Two rules: a phrase that introduces an imprint, and a bare number that is not a quantity.
IMPRINT_PHRASE = re.compile(r"\b(?:debossed|imprinted|engraved|embossed|stamped|marked|printed)\s+with\s+\S+|\bimprint(?:\s+code)?\s+\S+", re.I)
UNITS = r"(?:mg|mcg|µg|g|kg|ml|l|%|hours?|hrs?|days?|weeks?|months?|years?|yrs?|minutes?|mins?|units?|iu|meq|mmol|mm|cm|bpm|pounds?|lbs?|kilograms?|patients?|people|percent)\b"
QUANTITY_CONTEXT = re.compile(r"\b(?:under|over|aged?|than|above|below|least|most|up to|about|around|between|and|to|in|since|of)\s+\d+|\b(?:19|20)\d\d\b|\b[A-Z]\s+\d+\b", re.I)
BARE_NUMBER = re.compile(rf"(?<![\w.,/-])\d{{2,}}(?![\w.,/%-])(?!\s*{UNITS})", re.I)


def is_code_like_wide(query: str) -> bool:
    plain = QUANTITY_CONTEXT.sub(" ", query)  # ages, ranges, years and "Ammonia N 13" are not identifiers
    return is_code_like(query) or IMPRINT_PHRASE.search(query) is not None or BARE_NUMBER.search(plain) is not None


class AdaptiveHybridRetriever:
    """Hybrid of [keyword, dense] where the keyword list gets `code_weight` when the query contains an identifier.

    Dense embeddings carry no signal for codes, so equal-weight fusion lets that list push the right keyword hit down.
    """

    def __init__(self, keyword, dense, code_weight: float = 4.0, per_part: int = 50, rrf_k: int = 60, skip_dense: bool = False, detector=None):
        self.keyword, self.dense, self.code_weight, self.per_part, self.rrf_k = keyword, dense, code_weight, per_part, rrf_k
        self.detect = detector or is_code_like
        self.skip_dense = skip_dense  # code-like queries use the keyword list alone

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        code = self.detect(query)
        if code and self.skip_dense:
            return self.keyword.search(query, k, allowed=allowed)
        weights = [self.code_weight, 1.0] if code else [1.0, 1.0]
        lists = [self.keyword.search(query, self.per_part, allowed=allowed), self.dense.search(query, self.per_part, allowed=allowed)]
        return rrf(lists, k=self.rrf_k, top=k, weights=weights)


class Reranker:
    """Wraps a first-stage retriever; re-scores its top `depth` chunks with a cross-encoder."""

    def __init__(self, first_stage, chunks: list[Chunk], model_name: str = "BAAI/bge-reranker-base", depth: int = 30):
        from sentence_transformers import CrossEncoder

        self.first_stage, self.chunks, self.depth = first_stage, chunks, depth
        self.model = CrossEncoder(model_name, device=pick_device(), max_length=512)

    def predict(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        with MODEL_LOCK:
            return self.model.predict(pairs, batch_size=16)

    def search_scored(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[tuple[int, float]]:
        """Chunk positions with raw cross-encoder scores (logits), best first."""
        candidates = self.first_stage.search(query, self.depth, allowed=allowed)
        if not candidates:
            return []
        scores = np.asarray(self.predict([(query, self.chunks[i].embed_text) for i in candidates]))
        order = np.argsort(-scores)
        return [(candidates[i], float(scores[i])) for i in order[:k]]

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        return [pos for pos, _ in self.search_scored(query, k, allowed=allowed)]

"""Extractive cited answers with a refusal path. No text is generated: every sentence returned is a verbatim sentence
from a retrieved label chunk, shown with its source, so the answer cannot say anything the label does not.

    answerer = Answerer(reranker, chunks, threshold)
    answerer.answer("Is tolterodine safe in pregnancy?")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from hss.corpus import Chunk

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])|\n+")


@dataclass
class Citation:
    sentence: str
    drug: str
    section: str
    set_id: str
    chunk_key: tuple
    score: float


@dataclass
class Answer:
    supported: bool
    top_score: float
    citations: list[Citation] = field(default_factory=list)


def sentences(text: str, min_chars: int = 25) -> list[str]:
    return [s.strip() for s in SENTENCE.split(text) if len(s.strip()) >= min_chars]


class Answerer:
    def __init__(self, reranker, chunks: list[Chunk], threshold: float, n_chunks: int = 3, n_sentences: int = 2):
        self.reranker, self.chunks, self.threshold = reranker, chunks, threshold
        self.n_chunks, self.n_sentences = n_chunks, n_sentences

    def top_score(self, query: str) -> float:
        return self.reranker.search_scored(query, 1)[0][1]

    def answer(self, query: str) -> Answer:
        scored = self.reranker.search_scored(query, self.n_chunks)
        top = scored[0][1]
        if top < self.threshold:
            return Answer(False, top)  # refuse: nothing retrieved is close enough to the question
        pool = [(s, c) for pos, _ in scored for c in [self.chunks[pos]] for s in sentences(c.text)]
        scores = self.reranker.model.predict([(query, s) for s, _ in pool], batch_size=16)
        best = sorted(zip(scores, pool), key=lambda t: -t[0])[: self.n_sentences]
        cites = [Citation(s, c.doc_title, c.section_title, c.key[0], c.key, float(sc)) for sc, (s, c) in best]
        return Answer(True, top, cites)

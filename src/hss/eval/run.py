"""Score retrievers against the golden questions.

    uv run python -m hss.eval.run --retrievers bm25,bm25_hdr

Two ways to count a hit, because one question can be answered by several chunks:
  chunk    the exact chunk the question was written from (strict, underestimates)
  section  any chunk from the same section of the same drug (lenient)

Metrics (all with one relevant item per question, so they stay simple):
  recall@k   share of questions whose answer appears in the top k
  MRR@10     mean of 1/rank of the first hit, 0 if not in the top 10
  nDCG@10    1/log2(rank+1) of the first hit, 0 if not in the top 10
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path

from hss.corpus import Chunk, load_chunks
from hss.embed import index_path

GOLDEN = Path("eval/golden.jsonl")
RESULTS = Path("eval/results")
KS = (1, 5, 10, 20, 50)
MODEL = "BAAI/bge-small-en-v1.5"


def first_hit(ranking: list[int], chunks: list[Chunk], target, level: str) -> int | None:
    for rank, pos in enumerate(ranking, start=1):
        c = chunks[pos]
        if (c.key == target) if level == "chunk" else (c.section_key == target[:2]):
            return rank
    return None


def summarize(ranks: list[int | None]) -> dict:
    n = len(ranks)
    out = {f"recall@{k}": sum(r is not None and r <= k for r in ranks) / n for k in KS}
    out["mrr@10"] = sum(1 / r for r in ranks if r is not None and r <= 10) / n
    out["ndcg@10"] = sum(1 / math.log2(r + 1) for r in ranks if r is not None and r <= 10) / n
    return out


def build(name: str, chunks: list[Chunk], cache: dict):
    from hss import retrievers as R

    def get(key, factory):
        if key not in cache:
            cache[key] = factory()
        return cache[key]

    bm25 = lambda: get("bm25", lambda: R.BM25Retriever(chunks, with_header=False))
    bm25h = lambda: get("bm25_hdr", lambda: R.BM25Retriever(chunks, with_header=True))
    dense = lambda: get("dense", lambda: R.DenseRetriever(index_path(MODEL, True), MODEL))
    hybrid = lambda: get("hybrid", lambda: R.HybridRetriever([bm25h(), dense()]))
    return {
        "bm25": bm25,
        "bm25_hdr": bm25h,
        "dense": dense,
        "hybrid": hybrid,
        "hybrid_rerank": lambda: get("hybrid_rerank", lambda: R.Reranker(hybrid(), chunks)),
        "dense_rerank": lambda: get("dense_rerank", lambda: R.Reranker(dense(), chunks)),
    }[name]()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrievers", default="bm25,bm25_hdr")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    chunks = load_chunks()
    questions = [json.loads(line) for line in GOLDEN.read_text().splitlines()]
    RESULTS.mkdir(parents=True, exist_ok=True)
    cache: dict = {}

    for name in args.retrievers.split(","):
        retriever = build(name, chunks, cache)
        retriever.search(questions[0]["question"], 10)  # warm-up, excluded from timing
        chunk_ranks, section_ranks, latencies, per_question = [], [], [], []
        for q in questions:
            target = tuple(q["relevant_chunk"])
            start = time.perf_counter()
            ranking = retriever.search(q["question"], max(KS))
            latencies.append((time.perf_counter() - start) * 1000)
            cr = first_hit(ranking, chunks, target, "chunk")
            sr = first_hit(ranking, chunks, target, "section")
            chunk_ranks.append(cr)
            section_ranks.append(sr)
            per_question.append({"id": q["id"], "section": q["section"], "chunk_rank": cr, "section_rank": sr})

        result = {
            "retriever": name,
            "n_questions": len(questions),
            "chunk": summarize(chunk_ranks),
            "section": summarize(section_ranks),
            "latency_ms": {"p50": statistics.median(latencies), "p95": sorted(latencies)[int(0.95 * len(latencies)) - 1]},
            "per_question": per_question,
        }
        (RESULTS / f"{name}{args.tag}.json").write_text(json.dumps(result, indent=1))
        c, s, l = result["chunk"], result["section"], result["latency_ms"]
        print(
            f"{name:14s} chunk: R@1 {c['recall@1']:.2f} R@10 {c['recall@10']:.2f} R@50 {c['recall@50']:.2f} "
            f"MRR {c['mrr@10']:.3f} nDCG {c['ndcg@10']:.3f} | section: R@10 {s['recall@10']:.2f} MRR {s['mrr@10']:.3f} "
            f"| {l['p50']:.0f}/{l['p95']:.0f} ms"
        )


if __name__ == "__main__":
    main()

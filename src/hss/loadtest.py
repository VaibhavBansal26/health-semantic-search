"""Closed-loop load test of the real search stack: N workers each send a query, wait for the answer, send the next.

    uv run python -u -m hss.loadtest --corpus data/processed/corpus.jsonl.gz

Reports throughput and latency percentiles per concurrency level, for the routed hybrid (BM25 + dense) and the same
with the cross-encoder reranker on top, over the real 53k-chunk corpus on this machine's CPU/GPU. Queries are the 203
evaluation questions in a fixed shuffled order. In-process, so it measures the retrieval code, not HTTP or a network.
"""

from __future__ import annotations

import argparse
import json
import random
import resource
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from hss.corpus import load_chunks, load_chunks_file
from hss.eval.acl import GOLDENS
from hss.eval.run import RESULTS, build


def run_level(retriever, queries: list[str], workers: int, total: int) -> dict:
    lat: list[float] = []

    def one(q: str) -> None:
        t = time.perf_counter()
        retriever.search(q, 10)
        lat.append(time.perf_counter() - t)

    work = [queries[i % len(queries)] for i in range(total)]
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, work[: min(workers * 2, 8)]))  # warm-up, discarded
        lat.clear()
        start = time.perf_counter()
        list(pool.map(one, work))
        wall = time.perf_counter() - start
    ms = np.array(lat) * 1000
    return {"workers": workers, "requests": total, "qps": total / wall, "p50_ms": float(np.percentile(ms, 50)),
            "p95_ms": float(np.percentile(ms, 95)), "p99_ms": float(np.percentile(ms, 99)), "max_ms": float(ms.max())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--retrievers", default="hybrid_routed,adaptive_rerank")
    parser.add_argument("--workers", default="1,2,4,8")
    parser.add_argument("--requests", type=int, default=400, help="requests per level (reranker uses a quarter)")
    args = parser.parse_args()

    chunks = load_chunks_file(args.corpus) if args.corpus else load_chunks()
    queries = [json.loads(line)["question"] for g in GOLDENS for line in Path(g).read_text().splitlines()]
    random.Random(0).shuffle(queries)
    cache: dict = {}
    report: dict = {"n_chunks": len(chunks), "retrievers": {}}
    for name in args.retrievers.split(","):
        retriever = build(name, chunks, cache)
        retriever.search(queries[0], 10)
        total = args.requests // 4 if "rerank" in name else args.requests
        rows = []
        for w in (int(x) for x in args.workers.split(",")):
            row = run_level(retriever, queries, w, total)
            rows.append(row)
            print(f"{name:16s} {w} workers: {row['qps']:6.1f} QPS | p50 {row['p50_ms']:7.0f} p95 {row['p95_ms']:7.0f} "
                  f"p99 {row['p99_ms']:7.0f} ms | peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9:.1f} GB", flush=True)
        report["retrievers"][name] = rows
    report["peak_rss_gb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
    (RESULTS / "loadtest.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

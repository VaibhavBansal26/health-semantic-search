"""Why was one worker so much slower per query than four in the first 10M run? Test the order hypothesis.

    uv run python -u -m hss.scale_workers

Builds the same IVF4096 + PQ48 index over the same 10M synthetic vectors, then measures throughput with the worker counts
in this order: 1, 4, 8, 1, 1. If the first one-worker figure is much lower than the later ones, the cost is a warm-up
effect (pages and caches), not the single worker itself.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import faiss
import numpy as np

from hss.embed import index_path
from hss.scale import BLOCK, K, MODEL, block, percentile_ms

N = 10_000_000
OUT = Path("eval/results/scale_10m_workers.json")


def main() -> None:
    base = np.load(index_path(MODEL, True))
    dim = base.shape[1]
    queries = block(base, 99_999, 300)
    index = faiss.index_factory(dim, "IVF4096_HNSW32,PQ48x8", faiss.METRIC_INNER_PRODUCT)
    index.train(np.vstack([block(base, 50_000 + j, 100_000) for j in range(5)]))
    for b in range(N // BLOCK):
        index.add(block(base, b, BLOCK))
    ivf = faiss.extract_index_ivf(index)
    print("index built", flush=True)

    report: dict = {"order": [], "n": N}
    for nprobe in (4, 16):
        ivf.nprobe = nprobe
        for label, workers in (("1", 1), ("4", 4), ("8", 8), ("1 again", 1), ("1 third", 1)):
            work = queries.tolist() * 2
            lat: list[float] = []

            def one(q):
                s = time.perf_counter()
                index.search(np.asarray(q, dtype=np.float32)[None], K)
                lat.append(time.perf_counter() - s)

            with ThreadPoolExecutor(workers, initializer=lambda: faiss.omp_set_num_threads(1)) as pool:
                s = time.perf_counter()
                list(pool.map(one, work))  # no warm-up on purpose: the first call of each level is what we want to see
                wall = time.perf_counter() - s
            row = {"nprobe": nprobe, "workers": label, "qps": len(work) / wall, "p50_ms": percentile_ms(lat, 50), "p95_ms": percentile_ms(lat, 95)}
            report["order"].append(row)
            print(row, flush=True)
            OUT.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

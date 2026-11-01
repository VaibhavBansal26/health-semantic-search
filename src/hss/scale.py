"""Vector search at 10 million vectors on a laptop: what fits in memory, how fast, how much recall is lost.

    uv run python -u -m hss.scale --n 10000000

The 10M vectors are SYNTHETIC: each is a real chunk embedding plus Gaussian noise, re-normalised, generated block by
block from a fixed seed so the same vectors can be regenerated for exact search without storing them. Real text at this
size would not look the same (this data is more clustered than a real corpus, around 187 noisy copies of each real
vector), so treat recall as indicative and latency and memory as the more reliable numbers.

Index: IVF with 4096 cells (coarse cells found through an HNSW graph, so adding 10M vectors stays cheap) and product
quantization to 48 bytes per vector. Ground truth is exact inner-product search over all 10M, streamed in blocks.
"""

from __future__ import annotations

import argparse
import json
import resource
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import faiss
import numpy as np

from hss.embed import index_path

MODEL = "BAAI/bge-small-en-v1.5"
K = 10
BLOCK = 500_000
NOISE = 0.05


def peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9  # bytes on macOS


def block(base: np.ndarray, i: int, size: int) -> np.ndarray:
    rng = np.random.default_rng(1000 + i)
    v = base[rng.integers(0, len(base), size)] + rng.normal(0, NOISE, (size, base.shape[1])).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def exact_top_k(base: np.ndarray, n: int, queries: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    best_s = np.full((len(queries), K), -np.inf, dtype=np.float32)
    best_i = np.zeros((len(queries), K), dtype=np.int64)
    for b in range(n // BLOCK):
        flat = faiss.IndexFlatIP(base.shape[1])
        flat.add(block(base, b, BLOCK))
        s, i = flat.search(queries, K)
        s_all, i_all = np.hstack([best_s, s]), np.hstack([best_i, i + b * BLOCK])
        order = np.argsort(-s_all, axis=1)[:, :K]
        best_s, best_i = np.take_along_axis(s_all, order, 1), np.take_along_axis(i_all, order, 1)
    return best_i, best_s


def rescore(base: np.ndarray, found: np.ndarray, queries: np.ndarray) -> np.ndarray:
    """Exact inner product between each query and each id the index returned (vectors are regenerated from the seed)."""
    wanted = {int(i) for i in found.ravel() if i >= 0}
    vectors: dict[int, np.ndarray] = {}
    for b in sorted({i // BLOCK for i in wanted}):
        blk = block(base, b, BLOCK)
        for i in wanted:
            if i // BLOCK == b:
                vectors[i] = blk[i % BLOCK]
    return np.array([[float(queries[r] @ vectors[int(i)]) if i >= 0 else -1.0 for i in row] for r, row in enumerate(found)])


def percentile_ms(latencies: list[float], q: float) -> float:
    return float(np.percentile(latencies, q)) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=10_000_000)
    parser.add_argument("--queries", type=int, default=500)
    parser.add_argument("--pq", type=int, default=48, help="bytes per vector after product quantization")
    args = parser.parse_args()
    assert args.n % BLOCK == 0

    base = np.load(index_path(MODEL, True))
    dim = base.shape[1]
    report: dict = {"n": args.n, "dim": dim, "synthetic": True, "noise_sigma": NOISE, "bytes_per_vector_pq": args.pq}
    queries = block(base, 99_999, args.queries)

    t = time.time()
    index = faiss.index_factory(dim, f"IVF4096_HNSW32,PQ{args.pq}x8", faiss.METRIC_INNER_PRODUCT)
    index.train(np.vstack([block(base, 50_000 + j, 100_000) for j in range(5)]))  # 500k training vectors
    report["train_s"] = time.time() - t
    print(f"trained in {report['train_s']:.0f}s, peak RSS {peak_rss_gb():.1f} GB", flush=True)

    t = time.time()
    for b in range(args.n // BLOCK):
        index.add(block(base, b, BLOCK))
        if b % 4 == 3:
            print(f"  added {(b + 1) * BLOCK:,} in {time.time() - t:.0f}s, peak RSS {peak_rss_gb():.1f} GB", flush=True)
    report["add_s"] = time.time() - t
    report["peak_rss_gb_build"] = peak_rss_gb()

    out = Path(f"data/scale_index_{args.pq}.faiss")
    faiss.write_index(index, str(out))
    report["index_gb_on_disk"] = out.stat().st_size / 1e9
    out.unlink()
    print(f"index {report['index_gb_on_disk']:.2f} GB; exact ground truth next", flush=True)

    t = time.time()
    cache = Path(f"data/scale_truth_{args.n}_{args.queries}.npz")  # same seeded vectors every run, so the answer can be reused
    if cache.exists():
        z = np.load(cache)
        truth, truth_scores = z["ids"], z["scores"]
    else:
        truth, truth_scores = exact_top_k(base, args.n, queries)
        np.savez(cache, ids=truth, scores=truth_scores)
    kth = truth_scores[:, -1]
    report["exact_search_s_for_queries"] = time.time() - t
    report["exact_fp32_gb"] = args.n * dim * 4 / 1e9
    report["exact_fp16_gb"] = args.n * dim * 2 / 1e9
    report["exact_int8_gb"] = args.n * dim / 1e9

    report["nprobe"] = {}
    for nprobe in (4, 16, 64):
        index.nprobe = nprobe
        faiss.omp_set_num_threads(1)  # one thread per query; concurrency comes from the workers below
        lat, found = [], []
        for q in queries:
            s = time.perf_counter()
            found.append(index.search(q[None], K)[1][0])
            lat.append(time.perf_counter() - s)
        recall = float(np.mean([len(set(f) & set(t_)) / K for f, t_ in zip(found, truth)]))
        got = rescore(base, np.array(found), queries)
        near = float(np.mean(got >= (kth - 0.01)[:, None]))  # returned items about as good as the exact 10th best
        row = {"recall@10": recall, "near_recall@10": near, "mean_score_gap": float(np.mean(truth_scores - got)), "p50_ms": percentile_ms(lat, 50), "p95_ms": percentile_ms(lat, 95), "workers": {}}
        for workers in (1, 4, 8):
            rounds = queries.tolist() * 2
            lat_w: list[float] = []

            def one(q):
                s = time.perf_counter()
                index.search(np.asarray(q, dtype=np.float32)[None], K)
                lat_w.append(time.perf_counter() - s)

            s = time.perf_counter()
            # omp_set_num_threads is per calling thread, so each worker must pin itself to one OpenMP thread
            with ThreadPoolExecutor(workers, initializer=lambda: faiss.omp_set_num_threads(1)) as pool:
                list(pool.map(one, rounds[:50]))  # warm-up, discarded
                lat_w.clear()
                s = time.perf_counter()
                list(pool.map(one, rounds))
            wall = time.perf_counter() - s
            row["workers"][workers] = {"qps": len(rounds) / wall, "p95_ms": percentile_ms(lat_w, 95)}
        report["nprobe"][nprobe] = row
        w = row["workers"]
        print(f"nprobe {nprobe:3d}: recall@10 {recall:.3f} near {near:.3f} | 1 thread p50 {row['p50_ms']:.1f} p95 {row['p95_ms']:.1f} ms | "
              + " | ".join(f"{k}w {v['qps']:.0f} QPS p95 {v['p95_ms']:.0f} ms" for k, v in w.items()), flush=True)
    report["peak_rss_gb_total"] = peak_rss_gb()
    Path(f"eval/results/scale_{args.n // 1_000_000}m_pq{args.pq}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "nprobe"}, indent=1))


if __name__ == "__main__":
    main()

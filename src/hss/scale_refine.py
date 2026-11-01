"""10M vectors again, adding two things the first scale run left out: a refine step and filtered search.

    uv run python -u -m hss.scale_refine

Same synthetic data as hss.scale (real embeddings plus noise, regenerated from a seed). Index: IVF4096 + PQ48 as before,
wrapped in a refine stage that re-scores the best `k_factor * 10` PQ candidates against a 4-bit scalar-quantised copy of
the vectors (1.9 GB for 10M; an 8-bit copy would have been 3.8 GB, too much for this 8.6 GB machine).
Filtered search: only vectors whose id passes a mask may be returned, for masks allowing 50% and 5% of the ids; the
reference is exact search restricted to the same ids. Single-query latency and four-worker throughput are measured with
the first worker count repeated at the end, to test whether measurement order explains the odd one-worker numbers earlier.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import faiss
import numpy as np

from hss.embed import index_path
from hss.scale import BLOCK, K, MODEL, block, peak_rss_gb, percentile_ms

N = 10_000_000
QUERIES = 300
PQ = 48
OUT = Path("eval/results/scale_10m_refine.json")
TRUTH = Path("data/scale_truth_filtered_10000000_300.npz")


def make_masks() -> dict[str, np.ndarray | None]:
    r = np.random.default_rng(5).random(N)
    return {"none": None, "50%": r < 0.5, "5%": r < 0.05}


def truth_for_masks(base: np.ndarray, queries: np.ndarray, masks: dict) -> dict:
    """Exact top-K ids and scores for each mask, in one streaming pass over the 10M vectors."""
    best = {m: (np.full((len(queries), K), -np.inf, np.float32), np.zeros((len(queries), K), np.int64)) for m in masks}
    for b in range(N // BLOCK):
        blk = block(base, b, BLOCK)
        scores = queries @ blk.T  # (queries, BLOCK)
        for name, mask in masks.items():
            s = scores if mask is None else np.where(mask[b * BLOCK:(b + 1) * BLOCK][None, :], scores, -np.inf)
            top = np.argpartition(-s, K - 1, axis=1)[:, :K]
            ts = np.take_along_axis(s, top, 1)
            bs, bi = best[name]
            s_all, i_all = np.hstack([bs, ts]), np.hstack([bi, top + b * BLOCK])
            order = np.argsort(-s_all, axis=1)[:, :K]
            best[name] = (np.take_along_axis(s_all, order, 1), np.take_along_axis(i_all, order, 1))
        del scores, blk
        print(f"  exact pass block {b + 1}/{N // BLOCK}", flush=True)
    return best


def recall_and_near(found: np.ndarray, truth_ids: np.ndarray, truth_scores: np.ndarray, scores_of_found: np.ndarray) -> tuple[float, float]:
    strict = float(np.mean([len(set(f) & set(t)) / K for f, t in zip(found, truth_ids)]))
    near = float(np.mean(scores_of_found >= (truth_scores[:, -1] - 0.01)[:, None]))
    return strict, near


def rescore(base: np.ndarray, found: np.ndarray, queries: np.ndarray) -> np.ndarray:
    wanted = {int(i) for i in found.ravel() if i >= 0}
    vecs: dict[int, np.ndarray] = {}
    for b in sorted({i // BLOCK for i in wanted}):
        blk = block(base, b, BLOCK)
        for i in wanted:
            if i // BLOCK == b:
                vecs[i] = blk[i % BLOCK]
    return np.array([[float(queries[r] @ vecs[int(i)]) if i >= 0 else -1.0 for i in row] for r, row in enumerate(found)])


def main() -> None:
    base = np.load(index_path(MODEL, True))
    dim = base.shape[1]
    queries = block(base, 99_999, QUERIES)
    masks = make_masks()

    if TRUTH.exists():
        z = np.load(TRUTH)
        truth = {m: (z[f"s_{m}"], z[f"i_{m}"]) for m in masks}
    else:
        truth = truth_for_masks(base, queries, masks)
        np.savez(TRUTH, **{f"s_{m}": v[0] for m, v in truth.items()}, **{f"i_{m}": v[1] for m, v in truth.items()})
    print("ground truth ready", flush=True)

    report: dict = {"n": N, "pq_bytes": PQ, "refine": "4-bit scalar quantiser", "synthetic": True}
    index = faiss.index_factory(dim, f"IVF4096_HNSW32,PQ{PQ}x8", faiss.METRIC_INNER_PRODUCT)
    sample = np.vstack([block(base, 50_000 + j, 100_000) for j in range(5)])
    index.train(sample)
    ivf = faiss.extract_index_ivf(index)
    sq = faiss.IndexScalarQuantizer(dim, faiss.ScalarQuantizer.QT_4bit, faiss.METRIC_INNER_PRODUCT)
    sq.train(sample)
    refined = faiss.IndexRefine(index, sq)
    t = time.time()
    for b in range(N // BLOCK):
        refined.add(block(base, b, BLOCK))
        if b % 4 == 3:
            print(f"  added {(b + 1) * BLOCK:,} in {time.time() - t:.0f}s, peak RSS {peak_rss_gb():.1f} GB", flush=True)
    report["add_s"] = time.time() - t
    report["peak_rss_gb_build"] = peak_rss_gb()
    report["refine_store_gb"] = N * dim / 2 / 1e9

    def save() -> None:
        OUT.write_text(json.dumps(report, indent=1, default=float))

    def run(search, nprobe: int, mask_name: str) -> dict:
        ivf.nprobe = nprobe
        faiss.omp_set_num_threads(1)
        found, lat = [], []
        for q in queries:
            s = time.perf_counter()
            found.append(search(q[None])[0])
            lat.append(time.perf_counter() - s)
        found = np.array(found)
        ts, ti = truth[mask_name]
        strict, near = recall_and_near(found, ti, ts, rescore(base, found, queries))
        return {"recall@10": strict, "near_recall@10": near, "p50_ms": percentile_ms(lat, 50), "p95_ms": percentile_ms(lat, 95)}

    report["pq_only"], report["refined"], report["filtered"] = {}, {}, {}
    for nprobe in (16, 64):
        report["pq_only"][nprobe] = run(lambda q: index.search(q, K)[1], nprobe, "none")
        print(f"PQ48 only        nprobe {nprobe}: {report['pq_only'][nprobe]}", flush=True)
        save()
        for kf in (4, 10):
            refined.k_factor = kf
            report["refined"][f"nprobe{nprobe}_k{kf}"] = run(lambda q: refined.search(q, K)[1], nprobe, "none")
            print(f"PQ48 + refine    nprobe {nprobe} k_factor {kf}: {report['refined'][f'nprobe{nprobe}_k{kf}']}", flush=True)
            save()
        for name in ("50%", "5%"):
            mask = masks[name]
            packed = np.packbits(mask, bitorder="little")  # bit i of the bitmap = id i; keep the array alive while searching
            sel = faiss.IDSelectorBitmap(N, faiss.swig_ptr(packed))
            params = faiss.SearchParametersIVF(sel=sel, nprobe=nprobe)
            report["filtered"][f"{name}_nprobe{nprobe}"] = run(lambda q: index.search(q, K, params=params)[1], nprobe, name)
            print(f"PQ48 filtered {name:>3s} nprobe {nprobe}: {report['filtered'][f'{name}_nprobe{nprobe}']}", flush=True)
            save()

    # throughput, with the single-worker level measured first and again last
    ivf.nprobe = 16
    refined.k_factor = 4
    report["workers"] = {}
    for label, workers in (("1 (first)", 1), ("4", 4), ("8", 8), ("1 (last)", 1)):
        work = queries.tolist() * 2
        lat: list[float] = []

        def one(q):
            s = time.perf_counter()
            refined.search(np.asarray(q, dtype=np.float32)[None], K)
            lat.append(time.perf_counter() - s)

        with ThreadPoolExecutor(workers, initializer=lambda: faiss.omp_set_num_threads(1)) as pool:
            list(pool.map(one, work[:50]))
            lat.clear()
            s = time.perf_counter()
            list(pool.map(one, work))
            wall = time.perf_counter() - s
        report["workers"][label] = {"qps": len(work) / wall, "p95_ms": percentile_ms(lat, 95)}
        print(f"workers {label}: {report['workers'][label]}", flush=True)
    report["peak_rss_gb_total"] = peak_rss_gb()
    save()


if __name__ == "__main__":
    main()

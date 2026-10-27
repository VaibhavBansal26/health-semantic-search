"""Index benchmark: how much recall do we give up for speed and memory?

Ground truth is exact (brute-force) cosine search. Each approximate index is scored by
recall@10 against it: of the true 10 nearest chunks, how many did the index return?
Query vectors are 1,000 randomly chosen chunk vectors, which is a fair stand-in for
"queries that look like the corpus".

    uv run python -m hss.bench
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import faiss
import numpy as np
import psycopg

from hss.corpus import DEFAULT_DSN
from hss.embed import index_path

MODEL = "BAAI/bge-small-en-v1.5"
K = 10


def timed_search(search, queries: np.ndarray):
    latencies = []
    results = []
    for q in queries:
        start = time.perf_counter()
        ids = search(q)
        latencies.append((time.perf_counter() - start) * 1000)
        results.append(ids)
    return np.array(results), float(np.median(latencies)), float(np.percentile(latencies, 95))


def recall(found: np.ndarray, truth: np.ndarray) -> float:
    return float(np.mean([len(set(f) & set(t)) / K for f, t in zip(found, truth)]))


def main() -> None:
    vectors = np.load(index_path(MODEL, True))
    n, dim = vectors.shape
    rng = np.random.default_rng(0)
    queries = vectors[rng.choice(n, 1000, replace=False)]
    rows = []

    # exact search: the reference every other row is judged against
    flat = faiss.IndexFlatIP(dim)
    flat.add(vectors)
    truth, p50, p95 = timed_search(lambda q: flat.search(q[None], K)[1][0], queries)
    rows.append(("FAISS flat (exact, fp32)", 1.0, p50, p95, vectors.nbytes, 0.0))

    # int8 scalar quantisation: 4x smaller, still scans every vector
    sq = faiss.IndexScalarQuantizer(dim, faiss.ScalarQuantizer.QT_8bit, faiss.METRIC_INNER_PRODUCT)
    sq.train(vectors)
    sq.add(vectors)
    found, p50, p95 = timed_search(lambda q: sq.search(q[None], K)[1][0], queries)
    rows.append(("FAISS flat int8", recall(found, truth), p50, p95, n * dim, 0.0))

    # HNSW graph: sub-linear search; ef_search trades recall for latency
    start = time.time()
    hnsw = faiss.IndexHNSWFlat(dim, 16, faiss.METRIC_INNER_PRODUCT)
    hnsw.hnsw.efConstruction = 128
    hnsw.add(vectors)
    build = time.time() - start
    graph_bytes = n * dim * 4 + n * 16 * 2 * 4  # vectors + ~2M neighbour links of 4 bytes
    for ef in (16, 64, 128):
        hnsw.hnsw.efSearch = ef
        found, p50, p95 = timed_search(lambda q: hnsw.search(q[None], K)[1][0], queries)
        rows.append((f"FAISS HNSW M=16 ef={ef}", recall(found, truth), p50, p95, graph_bytes, build))

    # the same idea inside Postgres
    with psycopg.connect(DEFAULT_DSN, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("DROP TABLE IF EXISTS bench_vectors")
        conn.execute(f"CREATE TABLE bench_vectors (pos int PRIMARY KEY, embedding vector({dim}))")
        with conn.cursor().copy("COPY bench_vectors (pos, embedding) FROM STDIN") as copy:
            for i, v in enumerate(vectors):
                copy.write_row((i, "[" + ",".join(f"{x:.6f}" for x in v) + "]"))
        start = time.time()
        conn.execute("CREATE INDEX ON bench_vectors USING hnsw (embedding vector_ip_ops) WITH (m = 16, ef_construction = 64)")
        build = time.time() - start
        size = conn.execute("SELECT pg_total_relation_size('bench_vectors')").fetchone()[0]
        for ef in (16, 64, 128):
            conn.execute(f"SET hnsw.ef_search = {ef}")
            sql = f"SELECT pos FROM bench_vectors ORDER BY embedding <#> %s::vector LIMIT {K}"
            lit = lambda q: "[" + ",".join(f"{x:.6f}" for x in q) + "]"
            found, p50, p95 = timed_search(lambda q: [r[0] for r in conn.execute(sql, (lit(q),)).fetchall()], queries)
            rows.append((f"pgvector HNSW m=16 ef={ef}", recall(found, truth), p50, p95, size, build))
        conn.execute("DROP TABLE bench_vectors")

    print(f"{n} vectors, dim {dim}, {len(queries)} queries, k={K}\n")
    print(f"{'index':28s} {'recall@10':>9s} {'p50 ms':>8s} {'p95 ms':>8s} {'size MB':>8s} {'build s':>8s}")
    for name, rec, p50, p95, size, build in rows:
        print(f"{name:28s} {rec:9.3f} {p50:8.2f} {p95:8.2f} {size / 1e6:8.1f} {build:8.1f}")
    Path("eval/results").mkdir(parents=True, exist_ok=True)
    Path("eval/results/index_benchmark.json").write_text(json.dumps(
        [dict(index=r[0], recall_at_10=r[1], p50_ms=r[2], p95_ms=r[3], size_bytes=r[4], build_s=r[5]) for r in rows], indent=1))


if __name__ == "__main__":
    main()

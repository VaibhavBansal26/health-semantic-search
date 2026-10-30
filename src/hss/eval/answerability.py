"""Top reranker score for every answerable and unanswerable question, saved so thresholds can be studied offline.

    uv run python -m hss.eval.answerability
"""

from __future__ import annotations

import json
from pathlib import Path

from hss import retrievers as R
from hss.corpus import load_chunks_file
from hss.embed import index_path

MODEL = "BAAI/bge-small-en-v1.5"
OUT = Path("eval/results/answerability.json")


def main() -> None:
    chunks = load_chunks_file(Path("deploy/space/data/corpus.jsonl.gz"))
    bm25 = R.BM25Retriever(chunks, with_header=True)
    dense = R.DenseRetriever(index_path(MODEL, True), MODEL)
    rer = R.Reranker(R.AdaptiveHybridRetriever(bm25, dense, skip_dense=True), chunks)
    groups = {
        "answerable_v1": [json.loads(l)["question"] for l in Path("eval/golden.jsonl").read_text().splitlines()],
        "answerable_v2": [json.loads(l)["question"] for l in Path("eval/golden_v2.jsonl").read_text().splitlines()],
    }
    un = json.loads(Path("eval/unanswerable.json").read_text())
    groups["unanswerable_A"], groups["unanswerable_B"] = un["A"], un["B"]
    result = {g: [rer.search_scored(q, 1)[0][1] for q in qs] for g, qs in groups.items()}
    OUT.write_text(json.dumps(result, indent=1))
    for g, v in result.items():
        v = sorted(v)
        print(f"{g:15s} n={len(v)} min {v[0]:.2f} p25 {v[len(v)//4]:.2f} median {v[len(v)//2]:.2f} max {v[-1]:.2f}")


if __name__ == "__main__":
    main()

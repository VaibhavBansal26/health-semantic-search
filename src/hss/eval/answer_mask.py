"""Cited answers and refusal for a user who can read only a few labels.

    uv run python -u -m hss.eval.answer_mask

The user is granted the labels behind 40 randomly chosen answerable questions (about 5% of chunks). Two question groups:
  readable    the 40 questions written from those labels: how many are answered, and from the right label
  unreadable  40 other questions, whose answer lives in a label the user may not read: they should be refused, or answered
              from a readable label only (never from the hidden one, which the mask guarantees)
The same questions are also run as an admin, for comparison. Uses the corpus and embeddings the demo ships.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from hss import retrievers as R
from hss.acl import docs_mask
from hss.answer import Answerer
from hss.corpus import load_chunks_file
from hss.embed import index_path

MODEL = "BAAI/bge-small-en-v1.5"
THRESHOLD = 0.87
OUT = Path("eval/results/answer_mask.json")


def main() -> None:
    chunks = load_chunks_file(Path("deploy/space/data/corpus.jsonl.gz"))
    bm25 = R.BM25Retriever(chunks, with_header=True)
    dense = R.DenseRetriever(index_path(MODEL, True, True), MODEL)
    rer = R.Reranker(R.AdaptiveHybridRetriever(bm25, dense, skip_dense=True, detector=R.is_code_like_wide), chunks)
    answerer = Answerer(rer, chunks, THRESHOLD)
    questions = [json.loads(l) for f in ("eval/golden.jsonl", "eval/golden_v2.jsonl") for l in Path(f).read_text().splitlines()]
    granted = random.Random(0).sample(questions, 40)
    mask = docs_mask(chunks, {q["relevant_chunk"][0] for q in granted})
    others = [q for q in questions if q["relevant_chunk"][0] not in {g["relevant_chunk"][0] for g in granted}][:40]
    print(f"readable share {mask.mean():.1%}; {len(granted)} readable-topic and {len(others)} unreadable-topic questions", flush=True)

    report: dict = {"readable_share": float(mask.mean())}
    for group, qs in (("readable", granted), ("unreadable", others)):
        rows = []
        for q in qs:
            gold_label = q["relevant_chunk"][0]
            row = {"id": q["id"]}
            for who, m in (("narrow", mask), ("admin", None)):
                a = answerer.answer(q["question"], allowed=m)
                row[who] = {"supported": a.supported, "top": a.top_score,
                            "from_gold_label": [c.set_id == gold_label for c in a.citations],
                            "from_readable": [bool(mask[next(i for i, ch in enumerate(chunks) if ch.key == c.chunk_key)]) for c in a.citations]}
            rows.append(row)
        report[group] = rows
        n = len(rows)
        narrow = [r["narrow"] for r in rows]
        admin = [r["admin"] for r in rows]
        print(f"{group:10s} narrow: answered {sum(x['supported'] for x in narrow)}/{n}, first sentence from gold label "
              f"{sum(bool(x['from_gold_label']) and x['from_gold_label'][0] for x in narrow)}, any cite from unreadable label "
              f"{sum(not all(x['from_readable']) for x in narrow)} | admin: answered {sum(x['supported'] for x in admin)}/{n}, first from gold label "
              f"{sum(bool(x['from_gold_label']) and x['from_gold_label'][0] for x in admin)}", flush=True)
    OUT.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

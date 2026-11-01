"""Access control when a user can read only a few labels (a narrow tenant).

    uv run python -m hss.eval.acl_narrow --corpus data/processed/corpus.jsonl.gz

The user is granted the labels behind `--n` randomly chosen questions, a few percent of the corpus. Over-fetching and
filtering afterwards should break down here, because the top 50 rarely contains a readable chunk.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

from hss.acl import docs_mask
from hss.corpus import load_chunks, load_chunks_file
from hss.eval.acl import GOLDENS
from hss.eval.run import RESULTS, build, first_hit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrievers", default="bm25_hdr,dense,hybrid_routed")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--n", type=int, default=40)
    args = parser.parse_args()

    chunks = load_chunks_file(args.corpus) if args.corpus else load_chunks()
    questions = [json.loads(line) for g in GOLDENS for line in Path(g).read_text().splitlines()]
    granted = random.Random(0).sample(questions, args.n)
    others = [q for q in questions if q not in granted][: args.n]  # about labels the user may not read
    mask = docs_mask(chunks, {q["relevant_chunk"][0] for q in granted})
    print(f"granted {len({q['relevant_chunk'][0] for q in granted})} labels = {mask.mean():.1%} of chunks, {len(granted)} questions")
    cache: dict = {}
    report = {"readable_share": float(mask.mean()), "n_questions": len(granted), "retrievers": {}}
    for name in args.retrievers.split(","):
        retriever = build(name, chunks, cache)
        strategies = {
            "post-10": lambda q: [p for p in retriever.search(q, 10) if mask[p]],
            "post-50": lambda q: [p for p in retriever.search(q, 50) if mask[p]][:10],
            "post-200": lambda q: [p for p in retriever.search(q, 200) if mask[p]][:10],
            "pre": lambda q: retriever.search(q, 10, allowed=mask),
        }
        out = {}
        for sname, fn in strategies.items():
            hits, counts = 0, []
            for q in granted:
                ranking = fn(q["question"])
                counts.append(len(ranking))
                hits += first_hit(ranking, chunks, tuple(q["relevant_chunk"]), "chunk") is not None
            off = [len(fn(q["question"])) for q in others]  # questions about unreadable labels: how many results come back?
            out[sname] = {"recall@10": hits / len(granted), "mean_returned": statistics.mean(counts), "empty": sum(c == 0 for c in counts),
                          "unreadable_topic_mean_returned": statistics.mean(off), "unreadable_topic_empty": sum(c == 0 for c in off)}
        report["retrievers"][name] = out
        print(f"{name:14s} " + " | ".join(f"{s} R@10 {o['recall@10']:.2f} n {o['mean_returned']:.1f} empty {o['empty']} | off-topic n {o['unreadable_topic_mean_returned']:.1f} empty {o['unreadable_topic_empty']}" for s, o in out.items()))
    (RESULTS / "acl_narrow.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

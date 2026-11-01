"""Measure what access control does to retrieval.

    uv run python -m hss.eval.acl --corpus data/processed/corpus.jsonl.gz

For each retriever and each user (anonymous, one group, two groups, admin) we compare four ways of enforcing access:
  unfiltered   search as if everyone could read everything (shows how much would leak)
  post-10      search the top 10, then drop chunks the user may not read
  post-50      search the top 50, drop unreadable chunks, keep 10
  pre          exclude unreadable chunks while picking candidates (what the product does)

Recall is counted over questions whose answer the user is allowed to read, since that is the only answer they could
be given. Leakage counts returned chunks, and for `pre` it must be exactly zero.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np

from hss.acl import GROUPS, Principal, allowed_mask, doc_groups
from hss.corpus import load_chunks, load_chunks_file
from hss.eval.run import RESULTS, build, first_hit

GOLDENS = ("eval/golden.jsonl", "eval/golden_v2.jsonl", "eval/golden_v3.jsonl")
USERS = [
    Principal("anonymous"),
    Principal("one-group", {GROUPS[1]}),
    Principal("two-groups", {GROUPS[1], GROUPS[0]}),
    Principal("admin", admin=True),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrievers", default="bm25_hdr,dense,hybrid_routed,adaptive_rerank")
    parser.add_argument("--corpus", type=Path)
    args = parser.parse_args()

    chunks = load_chunks_file(args.corpus) if args.corpus else load_chunks()
    acls = doc_groups(chunks)
    questions = [json.loads(line) for g in GOLDENS for line in Path(g).read_text().splitlines()]
    masks = {u.name: allowed_mask(chunks, acls, u) for u in USERS}
    cache: dict = {}
    report = {"n_questions": len(questions), "users": {u.name: float(masks[u.name].mean()) for u in USERS}, "retrievers": {}}

    for name in args.retrievers.split(","):
        retriever = build(name, chunks, cache)
        retriever.search(questions[0]["question"], 10)
        rows = {}
        for user in USERS:
            mask = masks[user.name]
            key_pos = {c.key: c.pos for c in chunks}
            readable = [q for q in questions if mask[key_pos[tuple(q["relevant_chunk"])]]]
            hidden = [q for q in questions if not mask[key_pos[tuple(q["relevant_chunk"])]]]
            strategies = {
                "unfiltered": lambda q: retriever.search(q, 10),
                "post-10": lambda q: [p for p in retriever.search(q, 10) if mask[p]],
                "post-50": lambda q: [p for p in retriever.search(q, 50) if mask[p]][:10],
                "pre": lambda q: retriever.search(q, 10, allowed=mask),
            }
            out = {"n_readable": len(readable), "n_hidden": len(hidden)}
            for sname, fn in strategies.items():
                hits, returned, leaked, leaky_queries, times = 0, [], 0, 0, []
                for q in readable:
                    t = time.perf_counter()
                    ranking = fn(q["question"])
                    times.append((time.perf_counter() - t) * 1000)
                    returned.append(len(ranking))
                    bad = sum(not mask[p] for p in ranking)
                    leaked += bad
                    leaky_queries += bad > 0
                    hits += first_hit(ranking, chunks, tuple(q["relevant_chunk"]), "chunk") is not None
                gold_shown = 0
                for q in hidden:  # the user may not read the answer chunk: it must never come back
                    ranking = fn(q["question"])
                    gold_shown += first_hit(ranking, chunks, tuple(q["relevant_chunk"]), "chunk") is not None
                out[sname] = {
                    "recall@10": hits / len(readable),
                    "mean_returned": statistics.mean(returned),
                    "leaked_chunks": leaked,
                    "leaky_queries": leaky_queries,
                    "hidden_gold_returned": gold_shown,
                    "p50_ms": statistics.median(times),
                }
            rows[user.name] = out
            line = " | ".join(
                f"{s} R@10 {out[s]['recall@10']:.2f} n {out[s]['mean_returned']:.1f} leak {out[s]['leaked_chunks']}"
                for s in ("unfiltered", "post-10", "post-50", "pre")
            )
            print(f"{name:15s} {user.name:11s} ({len(readable)}/{len(questions)} readable) {line} | pre {out['pre']['p50_ms']:.0f} ms vs {out['unfiltered']['p50_ms']:.0f} ms")
        report["retrievers"][name] = rows
    (RESULTS / "acl.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

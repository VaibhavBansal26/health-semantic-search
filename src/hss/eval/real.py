"""Turn real questions into a scored question set (v6).

    uv run python -m hss.eval.real pool --queries data/queries.jsonl --out eval/real_sheet.json
    (a person opens the sheet and sets "relevant" on each question)
    uv run python -m hss.eval.real assemble --sheet eval/real_sheet.json --out eval/golden_v6.jsonl
    uv run python -m hss.eval.run --golden eval/golden_v6.jsonl --retrievers hybrid_routed_wide,dense_routed,rerank_d30 --tag _v6

`--queries` is either the query log written by `hss.service --query-log` or a plain text file with one question per line.

Pooling: the top few chunks from several different retrievers are merged, shuffled with a fixed seed and written to a
sheet, so the person labeling sees no rank and no retriever name. For each question they set "relevant" to the number of
the chunk that answers it, or to "none" if no listed chunk does. Only pooled chunks can be marked relevant, so a chunk that
no pooled retriever returned counts as not relevant; the sheet records how many questions had no answer so that is visible.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections.abc import Callable
from pathlib import Path

from hss.corpus import Chunk

Ranker = Callable[[str, int], list[int]]
SNIPPET = 500


def read_queries(path: Path) -> list[str]:
    """Distinct questions in first-seen order. Accepts the query log (JSON lines) or one question per line."""
    seen, out = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            line = json.loads(line)["query"]
        q = re.sub(r"\s+", " ", line).strip()
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def make_sheet(queries: list[str], rankers: dict[str, Ranker], chunks: list[Chunk], per: int = 3, seed: int = 0) -> list[dict]:
    """For each question, the union of every ranker's top `per` chunks in shuffled order, with no rank or source shown."""
    rng = random.Random(seed)
    sheet = []
    for i, q in enumerate(queries):
        pooled = list(dict.fromkeys(pos for rank in rankers.values() for pos in rank(q, per)))
        rng.shuffle(pooled)
        sheet.append({
            "id": f"r{i:03d}",
            "question": q,
            "relevant": None,
            "candidates": [{"n": n, "key": list(chunks[p].key), "drug": chunks[p].doc_title, "section": chunks[p].section_title,
                            "text": chunks[p].text[:SNIPPET]} for n, p in enumerate(pooled, start=1)],
        })
    return sheet


def assemble(sheet: list[dict]) -> tuple[list[dict], dict]:
    """Golden rows from a labeled sheet, and counts of what was kept and what was not."""
    kept, counts = [], {"labeled": 0, "none": 0, "unlabeled": 0}
    for item in sheet:
        label = item["relevant"]
        if label is None:
            counts["unlabeled"] += 1
        elif label == "none":
            counts["none"] += 1
        else:
            match = [c for c in item["candidates"] if c["n"] == label]
            if not match:
                raise ValueError(f"{item['id']}: relevant={label!r} is not one of the listed candidate numbers")
            counts["labeled"] += 1
            c = match[0]
            kept.append({"id": f"r{len(kept):03d}", "question": item["question"], "relevant_chunk": c["key"], "drug": c["drug"], "section": c["section"]})
    return kept, counts


def _rankers() -> tuple[dict[str, Ranker], list[Chunk]]:
    from hss.corpus import load_chunks
    from hss.eval.run import build

    chunks, cache = load_chunks(), {}
    names = ("bm25_hdr", "dense", "hybrid_routed_wide", "rerank_d30")
    return {n: build(n, chunks, cache).search for n in names}, chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    pool = sub.add_parser("pool")
    pool.add_argument("--queries", type=Path, required=True)
    pool.add_argument("--out", type=Path, default=Path("eval/real_sheet.json"))
    pool.add_argument("--per", type=int, default=3, help="chunks taken from each retriever per question")
    asm = sub.add_parser("assemble")
    asm.add_argument("--sheet", type=Path, default=Path("eval/real_sheet.json"))
    asm.add_argument("--out", type=Path, default=Path("eval/golden_v6.jsonl"))
    args = parser.parse_args()

    if args.cmd == "pool":
        queries = read_queries(args.queries)
        rankers, chunks = _rankers()
        sheet = make_sheet(queries, rankers, chunks, args.per)
        args.out.write_text(json.dumps(sheet, indent=1), encoding="utf-8")
        print(f"{len(sheet)} questions, {sum(len(s['candidates']) for s in sheet) / max(len(sheet), 1):.1f} candidates each -> {args.out}")
    else:
        kept, counts = assemble(json.loads(args.sheet.read_text(encoding="utf-8")))
        args.out.write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
        print(f"{len(kept)} questions written to {args.out}; {counts}")
        if counts["unlabeled"]:
            print("warning: unlabeled questions were skipped; set \"relevant\" on each, or \"none\"")


if __name__ == "__main__":
    main()

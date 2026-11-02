"""Assemble the natural-wording question set (v5) from candidate chunks and independently written questions.

    uv run python -m hss.eval.natural --candidates cand.jsonl --questions q_part_00.json q_part_01.json q_part_02.json

The questions were written by a model that saw only the chunk, never a search result, so they were not tuned to what the
engine finds. They are still model-written, and the wording rule (no run of 3 words copied from the chunk) is checked
here, not trusted: any question that copies one is dropped and counted. The report compares word overlap with the
chunk against the earlier sets, so "less templated" is a measured claim.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def copied_run(question: str, chunk: str, drug: str, n: int = 3) -> bool:
    """True if the question repeats n consecutive words from the chunk, ignoring runs made only of the drug's own name."""
    skip = set(words(drug))
    q, c = words(question), words(chunk)
    chunk_runs = {tuple(c[i : i + n]) for i in range(len(c) - n + 1)}
    return any(tuple(q[i : i + n]) in chunk_runs and not set(q[i : i + n]) <= skip for i in range(len(q) - n + 1))


def overlap(question: str, chunk: str) -> float:
    """Share of the question's words (3+ letters) that also occur in the chunk."""
    q = [w for w in words(question) if len(w) >= 3]
    c = set(words(chunk))
    return sum(w in c for w in q) / len(q) if q else 0.0


def mean_overlap(golden: Path, chunks: dict) -> float:
    rows = [json.loads(line) for line in golden.read_text().splitlines()]
    vals = [overlap(r["question"], chunks[tuple(r["relevant_chunk"])]) for r in rows if tuple(r["relevant_chunk"]) in chunks]
    return sum(vals) / len(vals)


def main() -> None:
    from hss.corpus import load_chunks

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--questions", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=Path("eval/golden_v5.jsonl"))
    args = parser.parse_args()

    cands = {}
    for line in args.candidates.read_text().splitlines():
        row = json.loads(line)
        cands[row["n"]] = row
    questions: dict[int, str | None] = {}
    for path in args.questions:
        questions.update({int(k): v for k, v in json.loads(path.read_text()).items()})

    chunks = {c.key: c.text for c in load_chunks()}
    kept, texts, dropped = [], [], {"null": 0, "copied": 0, "missing": 0, "duplicate": 0}
    seen: set[str] = set()
    for n, row in sorted(cands.items()):
        q = (questions.get(n) or "").strip()
        if not q:
            dropped["null" if n in questions else "missing"] += 1
        elif copied_run(q, row["text"], row["drug"]):
            dropped["copied"] += 1
        elif q.lower() in seen:
            dropped["duplicate"] += 1
        else:
            seen.add(q.lower())
            texts.append(row["text"])
            kept.append({"id": f"n{len(kept):03d}", "question": q, "relevant_chunk": row["key"], "drug": row["drug"], "section": row["section"]})
    args.out.write_text("".join(json.dumps(r) + "\n" for r in kept))

    mine = sum(overlap(r["question"], t) for r, t in zip(kept, texts)) / len(kept)
    print(f"kept {len(kept)} of {len(cands)} candidates; dropped {dropped}")
    print(f"mean word overlap with the answer chunk: v5 {mine:.2f}", end="")
    for name in ("golden.jsonl", "golden_v2.jsonl"):
        print(f", {name} {mean_overlap(Path('eval') / name, chunks):.2f}", end="")
    print()


if __name__ == "__main__":
    main()

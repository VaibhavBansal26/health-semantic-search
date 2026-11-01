"""Do retrievers tell "what is X for" from "who must not take X"?

    uv run python -u -m hss.eval.negation --corpus data/processed/corpus.jsonl.gz

For labels that have both an Indications and a Contraindications section, ask one question aimed at each, about the same
drug. A hit is a result from the right section of that label. The "confused" count is how often the best-ranked result
from that label is from the opposite section, i.e. the negation was ignored.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from hss.corpus import load_chunks, load_chunks_file
from hss.eval.run import RESULTS, build


def kind(title: str) -> str | None:
    t = title.upper()
    if "CONTRAINDICATION" in t:
        return "contra"
    if "INDICATIONS" in t:
        return "ind"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--retrievers", default="bm25_hdr,dense,hybrid_routed_wide,adaptive_rerank")
    parser.add_argument("--n", type=int, default=40)
    args = parser.parse_args()
    chunks = load_chunks_file(args.corpus) if args.corpus else load_chunks()

    kinds: dict[str, dict[str, bool]] = {}
    titles: dict[str, str] = {}
    for c in chunks:
        k = kind(c.section_title)
        if k:
            kinds.setdefault(c.key[0], {})[k] = True
            titles[c.key[0]] = c.doc_title
    eligible = sorted(s for s, v in kinds.items() if len(v) == 2 and len(titles[s].split("[")[0].split()) <= 3)
    chosen = random.Random(0).sample(eligible, min(args.n, len(eligible)))
    questions = []
    for sid in chosen:
        name = titles[sid].split("[")[0].strip().rstrip(",")
        questions.append({"set_id": sid, "want": "ind", "q": f"What is {name} used to treat?"})
        questions.append({"set_id": sid, "want": "contra", "q": f"In which patients must {name} not be used?"})
    Path("eval/negation_questions.json").write_text(json.dumps(questions, indent=1))

    cache: dict = {}
    report = {}
    for name in args.retrievers.split(","):
        r = build(name, chunks, cache)
        stat = {w: {"n": 0, "hit10": 0, "confused": 0, "label_found": 0} for w in ("ind", "contra")}
        for q in questions:
            s = stat[q["want"]]
            s["n"] += 1
            rank = r.search(q["q"], 10)
            own = [chunks[p] for p in rank if chunks[p].key[0] == q["set_id"]]
            s["hit10"] += any(kind(c.section_title) == q["want"] for c in own)
            s["label_found"] += bool(own)
            s["confused"] += bool(own) and kind(own[0].section_title) not in (q["want"], None)
        report[name] = stat
        print(f"{name:18s} " + " | ".join(f"{w}: hit@10 {v['hit10']}/{v['n']} confused {v['confused']} (label seen {v['label_found']})" for w, v in stat.items()), flush=True)
    (RESULTS / "negation.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

"""Build the exact-match question set: lookups by identifier, the case where keyword search should win.

Templated on purpose. Each question names one literal identifier (NDC code, CAS number, tablet imprint)
that occurs in exactly one chunk of the corpus, so the answer is unambiguous and nothing is paraphrased.

    uv run python -m hss.eval.exact
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

from hss.corpus import load_chunks_file

CORPUS = Path("deploy/space/data/corpus.jsonl.gz")
OUT = Path("eval/golden_v3.jsonl")

KINDS = {
    "ndc": (re.compile(r"\bNDC[ :]*(\d{4,5}-\d{3,4}-\d{1,2})"), "What product is NDC {}?", 25),
    "cas": (re.compile(r"CAS[^\d]{0,20}(\d{2,7}-\d{2}-\d)"), "Which drug has CAS registry number {}?", 9),
    "imprint": (
        re.compile(r"(?:debossed|imprinted|engraved)\s+(?:with\s+)?[\"“']([^\"”']{3,15})[\"”']"),
        "Which tablet is debossed with {}?",
        16,
    ),
}


def main() -> None:
    chunks = load_chunks_file(CORPUS)
    rng = random.Random(11)
    used_docs: set[str] = set()
    rows = []
    for kind, (rx, template, n) in KINDS.items():
        owners: dict[str, list] = {}
        for c in chunks:
            for token in set(rx.findall(c.text)):
                owners.setdefault(token, []).append(c)
        unique = [(t, cs[0]) for t, cs in owners.items() if len(cs) == 1]
        rng.shuffle(unique)
        taken = 0
        for token, c in unique:
            if taken == n:
                break
            if c.key[0] in used_docs:
                continue
            used_docs.add(c.key[0])
            taken += 1
            rows.append({"id": f"x{len(rows):03d}", "question": template.format(token), "relevant_chunk": list(c.key),
                         "drug": c.doc_title, "section": c.section_title, "kind": kind})
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(len(rows), "questions ->", OUT, dict(Counter(r["kind"] for r in rows)))


if __name__ == "__main__":
    main()

"""Pick a stratified, reproducible sample of chunks to write golden questions for.

One chunk per drug, spread across the section types a clinician actually asks about.
The sample is a starting point for question writing, not the questions themselves.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from hss.corpus import load_chunks

# LOINC section code -> how many questions to write for that kind of section
QUOTA = {
    "34067-9": 14,  # indications
    "34070-3": 10,  # contraindications
    "34068-7": 14,  # dosage and administration
    "43685-7": 12,  # warnings and precautions
    "34084-4": 12,  # adverse reactions
    "34073-7": 10,  # drug interactions
    "43684-0": 10,  # use in specific populations
    "34089-3": 6,   # description
    "34090-1": 8,   # clinical pharmacology
    "34069-5": 4,   # how supplied
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--scale", type=float, default=1.0, help="multiply every section quota")
    parser.add_argument("--exclude", type=Path, help="golden file whose drugs must not be sampled again")
    parser.add_argument("--out", type=Path, default=Path("data/eval/candidates.jsonl"))
    args = parser.parse_args()
    seed, out = args.seed, args.out
    quota = {k: max(1, round(v * args.scale)) for k, v in QUOTA.items()}

    rng = random.Random(seed)
    chunks = [c for c in load_chunks() if c.loinc_code in quota and 60 <= len(c.text) // 4 <= 350]
    rng.shuffle(chunks)

    used_docs: set[str] = set()
    if args.exclude:
        used_docs = {json.loads(line)["relevant_chunk"][0] for line in args.exclude.read_text().splitlines()}
    taken: dict[str, int] = {}
    picked = []
    for c in chunks:
        if c.key[0] in used_docs or taken.get(c.loinc_code, 0) >= quota[c.loinc_code]:
            continue
        used_docs.add(c.key[0])
        taken[c.loinc_code] = taken.get(c.loinc_code, 0) + 1
        picked.append(c)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for i, c in enumerate(picked):
            f.write(json.dumps({"n": i, "key": list(c.key), "drug": c.doc_title,
                                "section": c.section_title, "text": c.text}) + "\n")
    print(f"{len(picked)} candidates -> {out}", taken)


if __name__ == "__main__":
    main()

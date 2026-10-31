"""Brand-name labels asked about by their generic name: the terminology gap a pharmacist or clinician hits daily.

For each label titled with a brand (ISENTRESS) whose description names the generic (raltegravir), ask about the
generic and expect the brand label's indications or contraindications chunk. The generic must not appear in any
document title, so no generic-named label is a competing correct answer. Templated, like the identifier set.

    uv run python -m hss.eval.brand
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from hss.corpus import load_chunks_file

CORPUS = Path("deploy/space/data/corpus.jsonl.gz")
OUT = Path("eval/golden_v4.jsonl")
# Titles that are generic names in capitals, not brands, found by reading the generated pairs (the regex also grabbed
# chemical fragments such as "isopropylamino" for them). Dropped after generation so the sample itself is unchanged.
NOT_BRANDS = {
    "ISOTRETINOIN", "NEOSTIGMINE METHYLSULFATE", "BENZPHETAMINE HYDROCHLORIDE", "COLESEVELAM HYDROCHLORIDE",
    "METOPROLOL TARTRATE", "ROCURONIUM BROMIDE", "CICLOPIROX", "BISOPROLOL FUMARATE", "PROGESTERONE", "FAMOTIDINE",
    "LEVOTHYROXINE SODIUM", "ROPINIROLE", "RILUZOLE", "DESVENLAFAXINE",
}
TEMPLATES = {
    "34067-9": "What is {} used for?",
    "34070-3": "What are the contraindications of {}?",
}


def generic_of(brand: str, description: str) -> str | None:
    b = re.escape(brand)
    m = re.search(rf"{b}[^.]{{0,40}}?\(([a-z][a-z\- ]{{4,40}})\)", description, re.I) or re.search(
        rf"{b}[^.]{{0,60}}?\bcontains? ([a-z][a-z\-]{{4,30}})\b", description, re.I
    )
    return m.group(1).lower().strip() if m else None


def main() -> None:
    chunks = load_chunks_file(CORPUS)
    titles: dict[str, str] = {}
    for c in chunks:
        titles.setdefault(c.key[0], c.doc_title)
    all_titles = " ".join(t.lower() for t in titles.values())
    desc = {}
    first = {}
    for c in chunks:
        if c.loinc_code == "34089-3":
            desc.setdefault(c.key[0], " ".join(c.text.split())[:300])
        if c.loinc_code in TEMPLATES and c.key[2] == 0:
            first.setdefault((c.key[0], c.loinc_code), c)
    rng = random.Random(5)
    docs = [d for d, t in titles.items() if t.isupper() and d in desc]
    rng.shuffle(docs)
    rows, per_kind = [], {k: 0 for k in TEMPLATES}
    for d in docs:
        brand = titles[d]
        g = generic_of(brand, desc[d])
        if not g or g in all_titles or g.split()[0] in brand.lower() or len(g) < 6:
            continue
        for code, template in TEMPLATES.items():
            c = first.get((d, code))
            if c and per_kind[code] < 20 and not any(r["drug"] == brand and r["loinc"] == code for r in rows):
                per_kind[code] += 1
                rows.append({"id": f"b{len(rows):03d}", "question": template.format(g), "relevant_chunk": list(c.key),
                             "drug": brand, "generic": g, "section": c.section_title, "loinc": code})
                break  # one question per label
    rows = [r for r in rows if r["drug"] not in NOT_BRANDS]
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(len(rows), "questions ->", OUT, per_kind)
    for r in rows[:8]:
        print(" ", r["question"], "->", r["drug"])


if __name__ == "__main__":
    main()

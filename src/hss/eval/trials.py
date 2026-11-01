"""Questions about clinical trials, to check that the trial documents are findable once indexed.

    uv run python -m hss.eval.trials            # builds eval/golden_trials.jsonl from Postgres

One question per randomly chosen trial, built from its title ("What is the study X about?"), so it is a templated
lookup like the brand/generic set: it shows that trial chunks are reachable, not how well real trial questions work.
The gold chunk is the trial's first chunk; the section-level number counts any chunk of the same section.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from hss.corpus import load_chunks

OUT = Path("eval/golden_trials.jsonl")


def main() -> None:
    chunks = load_chunks(source="clinicaltrials")
    firsts = [c for c in chunks if c.key[1:] == (0, 0)]
    rows = []
    for i, c in enumerate(random.Random(0).sample(firsts, 60)):
        rows.append({"id": f"t{i:03d}", "question": f"What is the clinical trial \"{c.doc_title}\" about?",
                     "relevant_chunk": list(c.key), "drug": c.doc_title, "section": c.section_title})
    OUT.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"{len(rows)} questions -> {OUT}")


if __name__ == "__main__":
    main()

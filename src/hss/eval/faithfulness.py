"""Attribution accuracy of cited answers, the measurable stand-in for faithfulness when no text is generated.

    uv run python -u -m hss.eval.faithfulness

Every answer sentence is copied from a retrieved chunk, so the checks are about whether the citation is honest and useful:
  verbatim       the sentence appears word for word in the chunk it cites (must be 100%)
  gold chunk     the cited chunk is the one the question was written from
  gold section   the cited chunk is from the same label section (same drug)
  gold label     the cited chunk is from the same label (same drug) at least
Questions come from the answerable golden sets (v1, v2); it uses the corpus and index the demo ships (generic-name aliases).
Per-sentence records are saved so the second-sentence rule can be studied offline without rerunning the model.
"""

from __future__ import annotations

import json
from pathlib import Path

from hss import retrievers as R
from hss.answer import Answerer
from hss.corpus import load_chunks_file
from hss.embed import index_path

MODEL = "BAAI/bge-small-en-v1.5"
THRESHOLD = 0.87
OUT = Path("eval/results/faithfulness.json")


def main() -> None:
    chunks = load_chunks_file(Path("deploy/space/data/corpus.jsonl.gz"))
    by_key = {c.key: c for c in chunks}
    bm25 = R.BM25Retriever(chunks, with_header=True)
    dense = R.DenseRetriever(index_path(MODEL, True, True), MODEL)
    rer = R.Reranker(R.AdaptiveHybridRetriever(bm25, dense, skip_dense=True), chunks)
    answerer = Answerer(rer, chunks, THRESHOLD, n_sentences=4)  # keep up to 4 so the sentence rule can be studied afterwards
    questions = [json.loads(l) for f in ("eval/golden.jsonl", "eval/golden_v2.jsonl") for l in Path(f).read_text().splitlines()]

    records = []
    for n, q in enumerate(questions, 1):
        gold = tuple(q["relevant_chunk"])
        ans = answerer.answer(q["question"])
        rec = {"id": q["id"], "set": "v1" if not q["id"].startswith("v") else "v2", "supported": ans.supported, "top_score": ans.top_score, "citations": []}
        for c in ans.citations:
            rec["citations"].append({
                "score": c.score, "sentence": c.sentence,
                "verbatim": c.sentence in by_key[c.chunk_key].text,
                "gold_chunk": c.chunk_key == gold, "gold_section": c.chunk_key[:2] == gold[:2], "gold_label": c.chunk_key[0] == gold[0],
            })
        records.append(rec)
        if n % 25 == 0:
            print(f"  {n}/{len(questions)}", flush=True)
    OUT.write_text(json.dumps(records, indent=1))


if __name__ == "__main__":
    main()

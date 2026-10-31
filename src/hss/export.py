"""Package the corpus and embeddings for the database-free demo in deploy/space/.

    uv run python -m hss.export                      # chunks from Postgres
    uv run python -m hss.export --aliases --corpus F  # chunks from a corpus file, generic names added to titles

Embeddings are stored as float16 (half the size, no measurable ranking change at this scale);
the app converts them back to float32 on load.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np

from hss.aliases import with_aliases
from hss.corpus import load_chunks, load_chunks_file, save_chunks
from hss.embed import index_path

SPACE = Path("deploy/space")
MODEL = "BAAI/bge-small-en-v1.5"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aliases", action="store_true")
    parser.add_argument("--corpus", type=Path, help="read chunks from a corpus file instead of Postgres")
    args = parser.parse_args()

    (SPACE / "data").mkdir(parents=True, exist_ok=True)
    chunks = load_chunks_file(args.corpus) if args.corpus else load_chunks()
    if args.aliases:
        plain = Path("data/processed/corpus.jsonl.gz")  # keep the un-aliased corpus the evaluation scripts expect
        plain.parent.mkdir(parents=True, exist_ok=True)
        if not plain.exists():
            save_chunks(chunks, plain)
        chunks = with_aliases(chunks, json.loads(Path("data/aliases.json").read_text()))
    save_chunks(chunks, SPACE / "data" / "corpus.jsonl.gz")
    vectors = np.load(index_path(MODEL, True, args.aliases))
    assert len(vectors) == len(chunks), "embeddings and corpus are out of sync; re-run hss.embed"
    np.save(SPACE / "data" / "embeddings_fp16.npy", vectors.astype(np.float16))

    package = SPACE / "hss"
    package.mkdir(exist_ok=True)
    for name in ("__init__.py", "corpus.py", "retrievers.py", "answer.py"):
        shutil.copy(Path("src/hss") / name, package / name)

    for path in sorted(SPACE.rglob("*")):
        if path.is_file():
            print(f"{path.stat().st_size / 1e6:8.1f} MB  {path}")


if __name__ == "__main__":
    main()

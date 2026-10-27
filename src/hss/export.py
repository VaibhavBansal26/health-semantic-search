"""Package the corpus and embeddings for the database-free demo in deploy/space/.

    uv run python -m hss.export

Embeddings are stored as float16 (half the size, no measurable ranking change at this scale);
the app converts them back to float32 on load.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from hss.corpus import load_chunks, save_chunks
from hss.embed import index_path

SPACE = Path("deploy/space")
MODEL = "BAAI/bge-small-en-v1.5"


def main() -> None:
    (SPACE / "data").mkdir(parents=True, exist_ok=True)
    chunks = load_chunks()
    save_chunks(chunks, SPACE / "data" / "corpus.jsonl.gz")
    vectors = np.load(index_path(MODEL, True))
    assert len(vectors) == len(chunks), "embeddings and corpus are out of sync; re-run hss.embed"
    np.save(SPACE / "data" / "embeddings_fp16.npy", vectors.astype(np.float16))

    package = SPACE / "hss"
    package.mkdir(exist_ok=True)
    for name in ("__init__.py", "corpus.py", "retrievers.py"):
        shutil.copy(Path("src/hss") / name, package / name)

    for path in sorted(SPACE.rglob("*")):
        if path.is_file():
            print(f"{path.stat().st_size / 1e6:8.1f} MB  {path}")


if __name__ == "__main__":
    main()

"""Embed every chunk with a sentence-transformers model and save the matrix to data/index/.

    uv run python -m hss.embed --model BAAI/bge-small-en-v1.5
    uv run python -m hss.embed --model BAAI/bge-small-en-v1.5 --no-header   # ablation

Row i of the saved matrix is the vector for corpus chunk i (see hss.corpus).
Vectors are L2-normalised, so cosine similarity is a plain dot product.
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import numpy as np

from hss.corpus import load_chunks

INDEX_DIR = Path("data/index")


def index_path(model: str, header: bool) -> Path:
    return INDEX_DIR / f"emb_{model.replace('/', '__')}_{'hdr' if header else 'raw'}.npy"


def main() -> None:
    from sentence_transformers import SentenceTransformer

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--no-header", action="store_true", help="embed raw chunk text only")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-seq-length", type=int, default=512)
    parser.add_argument("--limit", type=int, default=0, help="embed only the first N chunks (timing test)")
    args = parser.parse_args()

    chunks = load_chunks()
    if args.limit:
        chunks = chunks[: args.limit]
    texts = [c.text if args.no_header else c.embed_text for c in chunks]

    model = SentenceTransformer(args.model, device="mps")
    model.max_seq_length = args.max_seq_length

    # Work in shards saved to disk, so a crash or restart resumes instead of starting over.
    final = index_path(args.model, not args.no_header)
    shard_dir = INDEX_DIR / (final.stem + "_shards")
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard_size = 4096
    parts = []
    start = time.time()
    for i in range(0, len(texts), shard_size):
        shard = shard_dir / f"{i:07d}.npy"
        if shard.exists() and not args.limit:
            vectors = np.load(shard)  # finished before a restart
        else:
            vectors = model.encode(
                texts[i : i + shard_size], batch_size=args.batch_size, normalize_embeddings=True
            ).astype(np.float32)
            if not args.limit:
                np.save(shard, vectors)
            print(f"shard {i // shard_size + 1}/{-(-len(texts) // shard_size)} done, {time.time() - start:.0f}s", flush=True)
        parts.append(vectors)
    vectors = np.concatenate(parts)
    seconds = time.time() - start
    print(f"{len(texts)} chunks, dim {vectors.shape[1]}, {seconds:.0f}s ({len(texts) / seconds:.0f} chunks/s)")

    if not args.limit:
        np.save(final, vectors)
        shutil.rmtree(shard_dir)


if __name__ == "__main__":
    main()

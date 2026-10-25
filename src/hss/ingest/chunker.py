"""Split section text into retrieval chunks.

Rules, in order of preference:
  1. Never cross a section boundary (a chunk about dosing never contains warnings).
  2. Break on paragraph boundaries, so a chunk reads as complete thoughts.
  3. Only if one paragraph alone is too big, break on sentences, then on words.
  4. Consecutive chunks of one section share a small overlap, so a fact that sits
     on a boundary appears whole in at least one chunk.

Sizes are in approximate tokens (characters / 4). Stage 4 swaps this for the real
tokenizer of the chosen embedding model; the limits are parameters for that reason.
"""

from __future__ import annotations

import math
import re

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(])")


def approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _split_long(unit: str, max_tokens: int) -> list[str]:
    """Break an oversize paragraph into sentences, then words if a sentence is still too big."""
    pieces: list[str] = []
    for sentence in SENTENCE_SPLIT.split(unit):
        if approx_tokens(sentence) <= max_tokens:
            pieces.append(sentence)
            continue
        words, current = sentence.split(), []
        for word in words:
            if current and approx_tokens(" ".join(current + [word])) > max_tokens:
                pieces.append(" ".join(current))
                current = []
            current.append(word)
        if current:
            pieces.append(" ".join(current))
    return pieces


def chunk_section(text: str, max_tokens: int = 350, overlap_tokens: int = 50) -> list[str]:
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if approx_tokens(paragraph) <= max_tokens:
            units.append(paragraph)
        else:
            units.extend(_split_long(paragraph, max_tokens))

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for unit in units:
        unit_size = approx_tokens(unit) + 1  # +1 for the blank line that joins units
        if current and size + unit_size > max_tokens:
            chunks.append("\n\n".join(current))
            # Carry the tail of the finished chunk forward as overlap.
            tail: list[str] = []
            tail_size = 0
            for previous in reversed(current):
                previous_size = approx_tokens(previous) + 1
                if tail_size + previous_size > overlap_tokens:
                    break
                tail.insert(0, previous)
                tail_size += previous_size
            if tail_size + unit_size > max_tokens:
                tail, tail_size = [], 0
            current, size = tail, tail_size
        current.append(unit)
        size += unit_size
    if current:
        chunks.append("\n\n".join(current))
    return chunks

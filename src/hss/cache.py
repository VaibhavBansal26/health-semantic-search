"""Result cache that cannot serve one user's results to another.

The cache key includes a digest of the access mask, so two users share an entry only when they can read exactly the same
chunks. Keying on the query alone is the classic leak: the first user's results are served to everyone who asks later.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import numpy as np


def mask_digest(allowed: np.ndarray | None) -> str:
    return "all" if allowed is None else hashlib.blake2b(np.packbits(allowed).tobytes(), digest_size=16).hexdigest()


class CachedSearch:
    def __init__(self, retriever, maxsize: int = 1024):
        self.retriever, self.maxsize = retriever, maxsize
        self.entries: OrderedDict[tuple, list[int]] = OrderedDict()
        self.hits = 0

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        key = (query, k, mask_digest(allowed))
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return list(self.entries[key])
        result = self.retriever.search(query, k, allowed=allowed)
        self.entries[key] = list(result)
        if len(self.entries) > self.maxsize:
            self.entries.popitem(last=False)
        return result

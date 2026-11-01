"""Result cache that cannot serve one user's results to another.

The cache key includes a digest of the access mask, so two users share an entry only when they can read exactly the same
chunks. Keying on the query alone is the classic leak: the first user's results are served to everyone who asks later.

Permission changes need no invalidation: a revoked or granted user has a different mask, so a different key, and an entry
is only ever served to someone whose mask is identical to the one it was computed for. Content changes do: when the index
is rebuilt (a sync replaced documents, chunk positions moved) call `bump()`, which clears every entry.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import numpy as np


class IndexChanged(RuntimeError):
    """The index was swapped between building a caller's mask and using it; the request should be retried."""


def mask_digest(allowed: np.ndarray | None) -> str:
    return "all" if allowed is None else hashlib.blake2b(np.packbits(allowed).tobytes(), digest_size=16).hexdigest()


class CachedSearch:
    def __init__(self, retriever, maxsize: int = 1024):
        self.retriever, self.maxsize = retriever, maxsize
        self.entries: OrderedDict[tuple, list[int]] = OrderedDict()
        self.hits = 0
        self.version = 0

    def bump(self) -> None:
        """The index changed: forget everything computed against the old one."""
        self.version += 1
        self.entries.clear()

    def search(self, query: str, k: int = 50, allowed: np.ndarray | None = None) -> list[int]:
        key = (self.version, query, k, mask_digest(allowed))
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return list(self.entries[key])
        result = self.retriever.search(query, k, allowed=allowed)
        self.entries[key] = list(result)
        if len(self.entries) > self.maxsize:
            self.entries.popitem(last=False)
        return result

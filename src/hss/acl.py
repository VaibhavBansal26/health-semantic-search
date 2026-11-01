"""Document access control for retrieval.

DailyMed labels are public, so the access rules here are synthetic: each label is assigned a visibility from a hash of
its set_id, which makes the assignment deterministic and independent of the text. The point is the mechanism, not the
policy: a user sees a chunk when its document is public or shares at least one group with the user.

    mask = allowed_mask(chunks, doc_groups(chunks), Principal("u1", {"oncology"}))
    retriever.search(query, 10, allowed=mask)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from hss.corpus import Chunk

GROUPS = ("cardiology", "oncology", "neurology", "infectious-disease")
PUBLIC_SHARE = 0.5  # share of labels visible to everyone


@dataclass(frozen=True)
class Principal:
    name: str
    groups: frozenset[str] = field(default_factory=frozenset)
    admin: bool = False

    def __init__(self, name: str, groups=(), admin: bool = False):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "groups", frozenset(groups))
        object.__setattr__(self, "admin", admin)


def doc_acl(set_id: str) -> frozenset[str]:
    """Groups allowed to read this label; empty means public. 1 or 2 groups when restricted."""
    h = int.from_bytes(hashlib.sha256(set_id.encode()).digest()[:8], "big")
    if (h % 1000) / 1000 < PUBLIC_SHARE:
        return frozenset()
    first = (h >> 10) % len(GROUPS)
    groups = {GROUPS[first]}
    if (h >> 20) % 3 == 0:
        groups.add(GROUPS[(first + 1 + (h >> 30) % (len(GROUPS) - 1)) % len(GROUPS)])
    return frozenset(groups)


def doc_groups(chunks: list[Chunk]) -> dict[str, frozenset[str]]:
    return {c.key[0]: doc_acl(c.key[0]) for c in chunks}


def allowed_mask(chunks: list[Chunk], acls: dict[str, frozenset[str]], who: Principal) -> np.ndarray:
    """Boolean mask over chunk positions: True where `who` may read the chunk."""
    if who.admin:
        return np.ones(len(chunks), dtype=bool)
    ok = {sid: (not g) or bool(g & who.groups) for sid, g in acls.items()}
    return np.fromiter((ok[c.key[0]] for c in chunks), dtype=bool, count=len(chunks))


def docs_mask(chunks: list[Chunk], set_ids: set[str]) -> np.ndarray:
    """Mask for a user granted an explicit list of labels (a narrow tenant)."""
    return np.fromiter((c.key[0] in set_ids for c in chunks), dtype=bool, count=len(chunks))

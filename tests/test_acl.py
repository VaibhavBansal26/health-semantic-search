import numpy as np

from hss.acl import GROUPS, Principal, allowed_mask, doc_acl, doc_groups
from hss.corpus import Chunk
from hss.retrievers import BM25Retriever, rrf


def make_chunks(n_docs: int = 40) -> list[Chunk]:
    return [
        Chunk(pos=i, key=(f"set-{i}", 0, 0), doc_title=f"DRUG{i}", section_title="Indications",
              loinc_code=None, text=f"DRUG{i} treats hypertension and edema in adults.")
        for i in range(n_docs)
    ]


def test_assignment_is_deterministic_and_mixed():
    assert doc_acl("set-1") == doc_acl("set-1")
    acls = [doc_acl(f"set-{i}") for i in range(200)]
    assert any(not a for a in acls) and any(a for a in acls)
    assert all(a <= set(GROUPS) and len(a) <= 2 for a in acls)


def test_mask_follows_group_membership():
    chunks = make_chunks()
    acls = doc_groups(chunks)
    anon = allowed_mask(chunks, acls, Principal("anon"))
    member = allowed_mask(chunks, acls, Principal("m", {GROUPS[0]}))
    admin = allowed_mask(chunks, acls, Principal("root", admin=True))
    assert admin.all()
    assert (member >= anon).all() and member.sum() > anon.sum()
    for c in chunks:
        assert anon[c.pos] == (not acls[c.key[0]])


def test_bm25_never_returns_masked_chunks_and_still_fills_the_list():
    chunks = make_chunks()
    mask = allowed_mask(chunks, doc_groups(chunks), Principal("anon"))
    bm25 = BM25Retriever(chunks)
    result = bm25.search("hypertension edema", 10, allowed=mask)
    assert len(result) == 10
    assert all(mask[p] for p in result)
    # a query matching nothing must not pad the list with masked zero-score chunks
    assert all(mask[p] for p in bm25.search("zzzunmatched", 10, allowed=mask))


def test_rrf_keeps_masked_inputs_out():
    assert rrf([[3, 1, 2], [2, 3]], top=5) == [3, 2, 1]

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


def test_cache_does_not_serve_one_users_results_to_another():
    from hss.cache import CachedSearch

    chunks = make_chunks()
    acls = doc_groups(chunks)
    admin = allowed_mask(chunks, acls, Principal("root", admin=True))
    anon = allowed_mask(chunks, acls, Principal("anon"))
    cached = CachedSearch(BM25Retriever(chunks))
    first = cached.search("hypertension edema", 10, allowed=admin)
    assert any(not anon[p] for p in first)  # the admin sees chunks the anonymous user may not
    second = cached.search("hypertension edema", 10, allowed=anon)
    assert cached.hits == 0 and all(anon[p] for p in second)
    assert cached.search("hypertension edema", 10, allowed=admin) == first and cached.hits == 1


def test_cache_shares_entries_between_users_with_identical_access():
    from hss.cache import CachedSearch

    chunks = make_chunks()
    acls = doc_groups(chunks)
    cached = CachedSearch(BM25Retriever(chunks))
    cached.search("edema", 5, allowed=allowed_mask(chunks, acls, Principal("a")))
    cached.search("edema", 5, allowed=allowed_mask(chunks, acls, Principal("b")))
    assert cached.hits == 1


def test_answerer_passes_the_mask_and_refuses_when_nothing_is_readable():
    from hss.answer import Answerer

    chunks = make_chunks(5)

    class Stub:
        seen = None

        def search_scored(self, query, k, allowed=None):
            Stub.seen = allowed
            return [] if allowed is not None and not allowed.any() else [(0, 5.0)]

    answerer = Answerer(Stub(), chunks, threshold=0.0)
    none = np.zeros(len(chunks), dtype=bool)
    result = answerer.answer("anything", allowed=none)
    assert Stub.seen is none and not result.supported and not result.citations
    assert answerer.top_score("anything", allowed=none) == float("-inf")


def test_cache_follows_permission_changes_without_explicit_invalidation():
    from hss.cache import CachedSearch

    chunks = make_chunks()
    acls = doc_groups(chunks)
    cached = CachedSearch(BM25Retriever(chunks))
    before = allowed_mask(chunks, acls, Principal("u", {GROUPS[0], GROUPS[1]}))
    first = cached.search("hypertension edema", 20, allowed=before)
    revoked = allowed_mask(chunks, acls, Principal("u", {GROUPS[0]}))  # one group taken away
    second = cached.search("hypertension edema", 20, allowed=revoked)
    assert cached.hits == 0 and all(revoked[p] for p in second)
    assert any(not revoked[p] for p in first)  # the old entry would have leaked the revoked labels


def test_cache_bump_drops_entries_after_the_index_changes():
    from hss.cache import CachedSearch

    chunks = make_chunks()
    cached = CachedSearch(BM25Retriever(chunks))
    cached.search("edema", 5)
    cached.search("edema", 5)
    assert cached.hits == 1
    cached.bump()
    cached.search("edema", 5)
    assert cached.hits == 1 and len(cached.entries) == 1

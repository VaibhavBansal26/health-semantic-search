from datetime import date

from hss.sync.core import Record
from hss.sync.ctgov import to_record

STUDY = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00000001", "briefTitle": "A trial"},
        "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-10-29"}},
        "descriptionModule": {"briefSummary": " Summary text. "},
        "eligibilityModule": {"eligibilityCriteria": "Adults."},
    }
}


def test_study_becomes_record_with_nonempty_sections_only():
    rec = to_record(STUDY)
    assert rec.doc_id == "NCT00000001" and rec.updated == date(2026, 10, 29)
    assert [t for t, _ in rec.sections] == ["Brief Summary", "Eligibility Criteria"]
    assert rec.sections[0][1] == "Summary text."


def test_study_without_text_is_dropped():
    empty = {"protocolSection": {**STUDY["protocolSection"], "descriptionModule": {}, "eligibilityModule": {}}}
    assert to_record(empty) is None


def test_content_hash_changes_with_content_not_with_date():
    a = Record("X", "T", (("S", "text"),), date(2026, 1, 1))
    assert a.content_hash == Record("X", "T", (("S", "text"),), date(2026, 2, 1)).content_hash
    assert a.content_hash != Record("X", "T", (("S", "text!"),), date(2026, 1, 1)).content_hash


def test_openfda_label_becomes_record_and_skips_empty():
    from hss.sync.openfda import to_record as of_record

    label = {"set_id": "abc", "effective_time": "20261020", "openfda": {"brand_name": ["BRANDX"]},
             "indications_and_usage": ["Used for   pain."], "contraindications": [" "]}
    rec = of_record(label)
    assert rec.doc_id == "abc" and rec.title == "BRANDX" and rec.updated == date(2026, 10, 20)
    assert rec.sections == (("Indications and Usage", "Used for   pain."),)
    assert of_record({"set_id": "x", "effective_time": "20261020"}) is None


def _chunk(i, text, title="Drug"):
    from hss.corpus import Chunk

    return Chunk(pos=i, key=(f"s{i}", 0, 0), doc_title=title, section_title="Sec", loinc_code=None, text=text)


def test_build_reuses_unchanged_vectors_and_embeds_only_new_text():
    import numpy as np

    from hss.reindex import build

    old = [_chunk(0, "alpha"), _chunk(1, "beta")]
    old_vectors = np.array([[1, 0], [0, 1]], np.float32)
    seen = []

    def embed(texts):
        seen.extend(texts)
        return np.full((len(texts), 2), 0.5, np.float32)

    new = [_chunk(0, "beta"), _chunk(1, "gamma"), _chunk(2, "alpha")]
    vectors, reused, embedded = build(new, old, old_vectors, embed)
    assert (reused, embedded) == (2, 1) and seen == [new[1].embed_text]
    assert vectors.tolist() == [[0, 1], [0.5, 0.5], [1, 0]]


def test_publish_switches_atomically_and_keeps_two_versions(tmp_path):
    import numpy as np

    from hss.corpus import load_chunks_file
    from hss.reindex import current, publish, read_meta

    seen = []
    for n in range(4):
        chunks = [_chunk(0, f"text {n}")]
        seen.append(publish(tmp_path, chunks, np.ones((1, 2), np.float32), {"signature": str(n)}))
        import time; time.sleep(0.002)
    live = current(tmp_path)
    assert live == seen[-1] and read_meta(live)["signature"] == "3"
    assert load_chunks_file(live / "corpus.jsonl.gz")[0].text == "text 3"
    assert sorted(p.name for p in tmp_path.glob("v*")) == sorted(p.name for p in seen[-2:])


def test_engine_rejects_a_mask_built_for_an_older_index(tmp_path):
    import numpy as np

    from hss.acl import Principal
    from hss.cache import CachedSearch, IndexChanged
    from hss.corpus import save_chunks
    from hss.reindex import publish
    from hss.service import HssEngine

    class FakeRetriever:
        def __init__(self, tag): self.tag = tag
        def search(self, query, k, allowed=None): return [0]

    eng = HssEngine.__new__(HssEngine)
    import threading
    eng.tenants = eng.section_rules = None
    eng.live_root, eng._swap, eng._loaded = tmp_path, threading.Lock(), None
    eng.chunks = [_chunk(0, "old")]
    eng.caches = {"hybrid": CachedSearch(FakeRetriever("old"))}
    eng.caches["hybrid"].search("q", 5)
    assert eng.caches["hybrid"].entries
    mask = np.ones(1, bool)
    eng.chunks = [_chunk(0, "a"), _chunk(1, "b")]  # index grew after the caller's mask was built
    try:
        eng.search("hybrid", "q", 5, mask)
        raise AssertionError("expected IndexChanged")
    except IndexChanged:
        pass

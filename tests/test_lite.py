import gzip
import hashlib
import json

import numpy as np
import pytest

from hss.acl import Principal
from hss.corpus import Chunk, save_chunks
from hss.lite import LiteEngine, build

DRUGS = ["Warfarin Sodium", "Ibuprofen", "Metformin", "Lisinopril", "Atorvastatin", "Sertraline", "Amoxicillin", "Omeprazole"]


def corpus(n_docs=40):
    chunks = []
    for i in range(n_docs):
        drug = DRUGS[i % len(DRUGS)]
        for j, section in enumerate(("INDICATIONS", "WARNINGS")):
            chunks.append(Chunk(pos=len(chunks), key=(f"set-{i}", j, 0), doc_title=drug, section_title=section, loinc_code=None,
                                text=f"{drug} {section.lower()} text number {i} about dosing and safety in adults"))
    return chunks


def fake_embed(text):
    """Deterministic 256-dim bag-of-words vector (md5, not hash(), which changes per process), so dense search has something to find."""
    vec = np.zeros(256, np.float32)
    for w in text.lower().split():
        vec[int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1.0
    return vec


@pytest.fixture()
def built(tmp_path):
    chunks = corpus()
    save_chunks(chunks, tmp_path / "corpus.jsonl.gz")
    vectors = np.array([fake_embed(c.embed_text) for c in chunks], np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    np.save(tmp_path / "emb.npy", vectors.astype(np.float16))
    info = build(tmp_path / "corpus.jsonl.gz", tmp_path / "emb.npy", tmp_path / "lite")
    return tmp_path / "lite", chunks, info


def test_build_writes_every_artifact(built):
    root, chunks, info = built
    assert info["chunks"] == len(chunks)
    assert {p.name for p in root.iterdir()} >= {"texts.sqlite", "meta.json.gz", "bm25", "embeddings_fp16.npy", "lite.json"}
    assert len(json.loads(gzip.open(root / "meta.json.gz", "rt").read())) == len(chunks)


def test_build_refuses_vectors_that_do_not_match_the_corpus(tmp_path):
    save_chunks(corpus(2), tmp_path / "c.jsonl.gz")
    np.save(tmp_path / "e.npy", np.zeros((3, 8), np.float16))
    with pytest.raises(ValueError):
        build(tmp_path / "c.jsonl.gz", tmp_path / "e.npy", tmp_path / "out")


def test_keyword_search_returns_text_read_from_disk(built):
    root, chunks, _ = built
    engine = LiteEngine(root, embed=lambda q: fake_embed(q))
    mask = engine.mask_for(Principal("admin", admin=True))
    hits = engine.search("keyword", "warfarin warnings", 5, mask)
    assert hits and all(h["drug"] == "Warfarin Sodium" for h in hits[:3])
    assert hits[0]["text"] == next(c.text for c in chunks if c.doc_title == hits[0]["drug"] and c.section_title == hits[0]["section"] and hits[0]["text"] == c.text)
    assert set(hits[0]) == {"label", "drug", "section", "text"}


def test_the_access_mask_removes_restricted_labels_from_every_mode(built):
    root, chunks, _ = built
    engine = LiteEngine(root, embed=lambda q: fake_embed(q))
    anon, admin = engine.mask_for(Principal("anon")), engine.mask_for(Principal("admin", admin=True))
    assert anon.sum() < admin.sum() == len(chunks)
    readable = {c.key[0] for c, ok in zip(chunks, anon) if ok}
    for mode in engine.modes:
        for query in ("dosing safety adults", "metformin indications", "text number 7"):
            assert {h["label"] for h in engine.search(mode, query, 10, anon)} <= readable


def test_dense_search_ranks_by_the_vectors_and_handles_an_empty_mask(built):
    root, chunks, _ = built
    engine = LiteEngine(root, embed=lambda q: fake_embed(q))
    mask = engine.mask_for(Principal("admin", admin=True))
    top = engine.search("dense", chunks[3].embed_text, 1, mask)[0]
    assert top["label"] == chunks[3].key[0] and top["section"] == chunks[3].section_title
    assert engine.search("dense", "anything", 5, np.zeros(len(chunks), bool)) == []


def test_answers_are_not_offered(built):
    root, _, _ = built
    engine = LiteEngine(root, embed=lambda q: fake_embed(q))
    assert engine.answers is False
    with pytest.raises(NotImplementedError):
        engine.answer("q", engine.mask_for(Principal("a")))

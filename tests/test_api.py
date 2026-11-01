import time

import jwt
import numpy as np
import pytest
from fastapi.testclient import TestClient

from hss.acl import GROUPS, Principal, allowed_mask, doc_groups
from hss.api import create_app
from hss.auth import AuthError, principal_from_token
from hss.corpus import Chunk
from hss.retrievers import BM25Retriever

KEY = "test-secret-with-enough-length-for-hs256-1234"
TENANT_DOCS = {"set-1": "acme", "set-2": "globex", "set-3": "acme", "set-4": "globex"}
SECTION_RULES = {"BOXED WARNING": frozenset({"pharmacy"})}


def make_chunks(n_docs: int = 40) -> list[Chunk]:
    chunks = []
    for i in range(n_docs):
        for j, section in enumerate(("Indications", "Boxed Warning")):
            chunks.append(Chunk(pos=len(chunks), key=(f"set-{i}", j, 0), doc_title=f"DRUG{i}", section_title=section,
                                loinc_code=None, text=f"DRUG{i} {section} text about hypertension and edema in adults."))
    return chunks


class StubEngine:
    modes = ("keyword", "hybrid")

    def __init__(self):
        self.chunks = make_chunks()
        self.acls = doc_groups(self.chunks)
        self.bm25 = BM25Retriever(self.chunks)

    def mask_for(self, who):
        return allowed_mask(self.chunks, self.acls, who, TENANT_DOCS, SECTION_RULES)

    def search(self, mode, query, k, mask):
        return [{"label": self.chunks[p].key[0], "section": self.chunks[p].section_title, "text": self.chunks[p].text}
                for p in self.bm25.search(query, k, allowed=mask)]

    def answer(self, query, mask):
        ok = bool(mask.any()) and "hypertension" in query
        return {"supported": ok, "top_score": 0.9 if ok else 0.1, "citations": []}


def token(sub="u", groups=(), roles=(), tenant=None, exp=None, key=KEY, alg="HS256"):
    claims = {"sub": sub, "groups": list(groups), "roles": list(roles), "exp": exp or int(time.time()) + 600}
    if tenant:
        claims["tenant"] = tenant
    return jwt.encode(claims, key, algorithm=alg)


@pytest.fixture()
def client():
    return TestClient(create_app(StubEngine(), KEY))


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def search(client, t, query="hypertension edema", k=20, **extra):
    return client.post("/search", json={"query": query, "k": k, **extra}, headers=auth(t))


def test_requests_without_a_valid_token_never_reach_search(client):
    assert client.post("/search", json={"query": "x"}).status_code == 401
    assert search(client, token(exp=int(time.time()) - 10)).status_code == 401  # expired
    assert search(client, token(key="another-secret-of-sufficient-length-5678")).status_code == 401  # wrong signature
    assert search(client, jwt.encode({"sub": "u", "exp": int(time.time()) + 60}, None, algorithm="none")).status_code == 401
    assert search(client, token()[:-3] + "abc").status_code == 401  # tampered
    assert client.post("/search", json={"query": "x"}, headers={"Authorization": "Basic abc"}).status_code == 401


def test_token_without_expiry_or_subject_is_rejected():
    for claims in ({"sub": "u"}, {"exp": int(time.time()) + 60}):
        with pytest.raises(AuthError):
            principal_from_token(jwt.encode(claims, KEY, algorithm="HS256"), KEY)


def test_groups_decide_what_comes_back(client):
    engine = StubEngine()
    anon = {r["label"] for r in search(client, token()).json()["results"]}
    member = {r["label"] for r in search(client, token(groups=[GROUPS[0], GROUPS[1]])).json()["results"]}
    for label in anon:
        assert not engine.acls[label] or label in TENANT_DOCS
    assert len(member) >= len(anon)


def test_a_tenant_never_sees_another_tenants_documents(client):
    acme = {r["label"] for r in search(client, token(tenant="acme", groups=GROUPS)).json()["results"]}
    globex = {r["label"] for r in search(client, token(tenant="globex", groups=GROUPS)).json()["results"]}
    nobody = {r["label"] for r in search(client, token(groups=GROUPS)).json()["results"]}
    assert not (acme & {"set-2", "set-4"}) and not (globex & {"set-1", "set-3"})
    assert not (nobody & set(TENANT_DOCS))


def test_section_rule_hides_boxed_warning_from_everyone_outside_the_group(client):
    plain = search(client, token(groups=GROUPS), query="Boxed Warning text").json()["results"]
    pharmacy = search(client, token(groups=[*GROUPS, "pharmacy"]), query="Boxed Warning text").json()["results"]
    assert plain and pharmacy
    assert all(r["section"] != "Boxed Warning" for r in plain)
    assert any(r["section"] == "Boxed Warning" for r in pharmacy)


def test_revoking_a_group_removes_what_only_that_group_could_read(client):
    engine = StubEngine()
    full, kept = Principal("u", GROUPS), Principal("u", GROUPS[:1])
    lost = {engine.chunks[i].key[0] for i in np.flatnonzero(engine.mask_for(full) & ~engine.mask_for(kept))}
    assert lost  # the revoked groups really did unlock some labels
    for query in ("hypertension edema", "DRUG3 Indications", "adults text"):
        labels = {r["label"] for r in search(client, token(groups=GROUPS[:1]), query=query).json()["results"]}
        assert not (labels & lost)


def test_not_found_log_is_admin_only_and_records_who_asked(client):
    client.post("/answer", json={"query": "unrelated cooking question"}, headers=auth(token(sub="alice", tenant="acme")))
    assert client.get("/admin/not-found", headers=auth(token())).status_code == 403
    assert client.get("/admin/not-found").status_code == 401
    rows = client.get("/admin/not-found", headers=auth(token(roles=["admin"]))).json()["rows"]
    assert rows and rows[0]["user"] == "alice" and rows[0]["tenant"] == "acme" and rows[0]["endpoint"] == "answer"


def test_admin_reads_everything_and_unknown_mode_is_rejected(client):
    labels = {r["label"] for r in search(client, token(roles=["admin"])).json()["results"]}
    assert labels & set(TENANT_DOCS) or len(labels) > 5
    assert search(client, token(), mode="nonsense").status_code == 422

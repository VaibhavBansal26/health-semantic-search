import json

import pytest
from fastapi.testclient import TestClient
from test_api import KEY, StubEngine, auth, token

from hss.api import create_app
from hss.corpus import Chunk
from hss.eval.real import assemble, make_sheet, read_queries
from hss.querylog import QueryLog


def chunks(n=6):
    return [Chunk(pos=i, key=(f"set-{i}", 0, 0), doc_title=f"DRUG{i}", section_title="Indications", loinc_code=None, text=f"text {i}") for i in range(n)]


def test_query_log_records_text_and_endpoint_but_not_the_user(tmp_path):
    path = tmp_path / "q.jsonl"
    client = TestClient(create_app(StubEngine(), KEY, query_log=QueryLog(path)))
    headers = auth(token(sub="alice@example.com"))
    client.post("/search", json={"query": "hypertension edema"}, headers=headers)
    client.post("/answer", json={"query": "hypertension dose"}, headers=headers)
    client.post("/search", json={"query": "ignored"})  # no token: never reaches search, never logged
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["endpoint"], r["query"]) for r in rows] == [("search", "hypertension edema"), ("answer", "hypertension dose")]
    assert "alice" not in path.read_text()


def test_no_log_file_unless_asked(tmp_path):
    client = TestClient(create_app(StubEngine(), KEY))
    assert client.post("/search", json={"query": "hypertension"}, headers=auth(token())).status_code == 200
    assert not list(tmp_path.iterdir())


def test_read_queries_dedupes_and_accepts_log_or_plain_lines(tmp_path):
    plain = tmp_path / "plain.txt"
    plain.write_text("Is  it safe?\n\nis it safe?\nWhat is the dose\n")
    assert read_queries(plain) == ["Is it safe?", "What is the dose"]
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"query": "a b", "endpoint": "search"}) + "\n" + json.dumps({"query": "A  B", "endpoint": "answer"}) + "\n")
    assert read_queries(log) == ["a b"]


def test_sheet_pools_without_duplicates_and_hides_the_source():
    rankers = {"one": lambda q, k: [0, 1, 2][:k], "two": lambda q, k: [2, 3, 4][:k]}
    sheet = make_sheet(["q"], rankers, chunks(), per=3)
    cands = sheet[0]["candidates"]
    assert sorted(c["key"][0] for c in cands) == [f"set-{i}" for i in range(5)]
    assert [c["n"] for c in cands] == [1, 2, 3, 4, 5]
    assert not {"rank", "found_by", "retriever"} & set(cands[0])
    assert make_sheet(["q"], rankers, chunks(), per=3) == sheet  # fixed seed: the same sheet every time


def test_assemble_keeps_labeled_counts_the_rest_and_rejects_bad_numbers():
    sheet = make_sheet(["a", "b", "c", "d"], {"one": lambda q, k: [0, 1]}, chunks(), per=2)
    sheet[0]["relevant"] = sheet[0]["candidates"][1]["n"]
    sheet[1]["relevant"] = "none"
    sheet[2]["relevant"] = None
    sheet[3]["relevant"] = 1
    kept, counts = assemble(sheet)
    assert counts == {"labeled": 2, "none": 1, "unlabeled": 1}
    assert kept[0]["question"] == "a" and kept[0]["relevant_chunk"] == sheet[0]["candidates"][1]["key"]
    assert {"id", "question", "relevant_chunk", "drug", "section"} <= set(kept[0])
    sheet[3]["relevant"] = 99
    with pytest.raises(ValueError):
        assemble(sheet)

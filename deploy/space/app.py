"""Demo UI: search ~53k chunks of FDA drug labels with four retrieval modes side by side.

Runs without a database: the corpus and the precomputed chunk embeddings ship with the app.
"""

from __future__ import annotations

import html
import time
from pathlib import Path

import gradio as gr
import numpy as np

from hss.corpus import load_chunks_file
from hss.answer import Answerer
from hss.retrievers import AdaptiveHybridRetriever, BM25Retriever, DenseRetriever, Reranker

DATA = Path(__file__).parent / "data"
MODEL = "BAAI/bge-small-en-v1.5"

chunks = load_chunks_file(DATA / "corpus.jsonl.gz")
np.save("/tmp/emb.npy", np.load(DATA / "embeddings_fp16.npy").astype(np.float32))  # dense retriever expects fp32

bm25 = BM25Retriever(chunks, with_header=True)
dense = DenseRetriever(Path("/tmp/emb.npy"), MODEL)
# fuses BM25 and dense; queries containing a code (NDC, CAS number, imprint) go to BM25 alone
hybrid = AdaptiveHybridRetriever(bm25, dense, skip_dense=True)
REFUSAL_THRESHOLD = 0.87  # reranker score below which we say there is no supported answer
_reranker: Reranker | None = None  # loaded on first use, the model is the biggest download


def get_retriever(mode: str):
    if mode == "Keyword (BM25)":
        return bm25
    if mode == "Meaning (dense embeddings)":
        return dense
    if mode == "Hybrid (BM25 + dense)":
        return hybrid
    return get_reranker()


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker(hybrid, chunks)
    return _reranker


def cited_answer(query: str) -> str:
    start = time.perf_counter()
    result = Answerer(get_reranker(), chunks, REFUSAL_THRESHOLD).answer(query)
    ms = (time.perf_counter() - start) * 1000
    if not result.supported:
        return (f"**No supported answer** ({ms:.0f} ms, relevance {result.top_score:.2f} below {REFUSAL_THRESHOLD}).\n\n"
                "Nothing in these labels is close enough to the question. Try rephrasing, or use a search mode to browse.")
    lines = [f"**Answer from the labels** ({ms:.0f} ms, relevance {result.top_score:.2f}). Sentences are quoted verbatim.\n"]
    for cite in result.citations:
        lines.append(
            f"> {html.escape(cite.sentence)}\n\n"
            f"— *{html.escape(cite.drug)}*, {html.escape(cite.section)} · label `{cite.set_id[:8]}`\n"
        )
    lines.append("Relevance-based: it can still answer a question about a real drug that the label does not actually address.")
    return "\n".join(lines)


def search(query: str, mode: str, top_k: int):
    if not query.strip():
        return "Type a question, for example: *Who should not take valacyclovir?*"
    if mode == "Cited answer":
        return cited_answer(query)
    start = time.perf_counter()
    ranking = get_retriever(mode).search(query, int(top_k))
    ms = (time.perf_counter() - start) * 1000

    cards = [f"**{len(ranking)} results in {ms:.0f} ms** using *{mode}*\n"]
    for rank, pos in enumerate(ranking, start=1):
        c = chunks[pos]
        snippet = html.escape(" ".join(c.text.split())[:700])
        cards.append(
            f"### {rank}. {html.escape(c.doc_title)}\n"
            f"*{html.escape(c.section_title)}* &nbsp;·&nbsp; label `{c.key[0][:8]}`\n\n{snippet}…\n"
        )
    return "\n".join(cards)


EXAMPLES = [
    ["Who should not take valacyclovir?", "Hybrid (BM25 + dense)"],
    ["Can patients on sildenafil use nitroglycerin sublingual tablets?", "Hybrid (BM25 + dense)"],
    ["How does mycophenolate work in the body?", "Meaning (dense embeddings)"],
    ["What happens if Adasuve is combined with alcohol?", "Keyword (BM25)"],
    ["Is tolterodine safe to take while pregnant?", "Cited answer"],
    ["What is the capital of Australia?", "Cited answer"],
]

with gr.Blocks(title="Health Semantic Search") as demo:
    gr.Markdown(
        "# Health Semantic Search\n"
        "Search 959 FDA prescription drug labels (DailyMed, public domain) and compare keyword, meaning, "
        "hybrid and reranked retrieval. Demo only: this is not medical advice."
    )
    with gr.Row():
        query = gr.Textbox(label="Question", placeholder="Who should not take valacyclovir?", scale=4)
        mode = gr.Radio(
            ["Keyword (BM25)", "Meaning (dense embeddings)", "Hybrid (BM25 + dense)", "Hybrid + reranker", "Cited answer"],
            value="Hybrid (BM25 + dense)", label="Retrieval mode", scale=3,
        )
        top_k = gr.Slider(1, 10, value=5, step=1, label="Results", scale=1)
    out = gr.Markdown()
    go = gr.Button("Search", variant="primary")
    go.click(search, [query, mode, top_k], out)
    query.submit(search, [query, mode, top_k], out)
    gr.Examples(EXAMPLES, [query, mode])

if __name__ == "__main__":
    demo.launch()

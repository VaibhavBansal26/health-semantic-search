"""Demo UI: search ~53k chunks of FDA drug labels with four retrieval modes side by side, plus cited answers.

Runs without a database: the corpus and the precomputed chunk embeddings ship with the app. A "Viewing as" selector
applies synthetic document-level access rules (the labels are public; the rules only demonstrate the mechanism), and
queries that found nothing are listed per session.
"""

from __future__ import annotations

import html
import time
from pathlib import Path

import gradio as gr
import numpy as np

from hss.acl import Principal, allowed_mask, doc_groups
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
acls = doc_groups(chunks)  # label -> groups allowed to read it (empty = public)
VIEWERS = {
    "Admin (every label)": Principal("admin", admin=True),
    "Oncology + cardiology member": Principal("two", {"oncology", "cardiology"}),
    "Oncology member": Principal("one", {"oncology"}),
    "Anonymous (public labels only)": Principal("anon"),
}
MASKS = {name: allowed_mask(chunks, acls, who) for name, who in VIEWERS.items()}
STATS = {"answered": 0, "refused": 0}  # counts only, no query text, shared across visitors
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


def lock_badge(set_id: str) -> str:
    groups = acls[set_id]
    return f" &nbsp;·&nbsp; 🔒 restricted to {', '.join(sorted(groups))}" if groups else ""


def cited_answer(query: str, mask: np.ndarray) -> tuple[str, float | None]:
    """Markdown answer plus the relevance score when the question was refused (None when answered)."""
    start = time.perf_counter()
    result = Answerer(get_reranker(), chunks, REFUSAL_THRESHOLD).answer(query, allowed=mask)
    ms = (time.perf_counter() - start) * 1000
    STATS["refused" if not result.supported else "answered"] += 1
    if not result.supported:
        return (f"**No supported answer** ({ms:.0f} ms, relevance {result.top_score:.2f} below {REFUSAL_THRESHOLD}).\n\n"
                "Nothing you can read is close enough to the question. Try rephrasing, or use a search mode to browse."), result.top_score
    lines = [f"**Answer from the labels** ({ms:.0f} ms, relevance {result.top_score:.2f}). Sentences are quoted verbatim.\n"]
    for cite in result.citations:
        lines.append(
            f"> {html.escape(cite.sentence)}\n\n"
            f"— *{html.escape(cite.drug)}*, {html.escape(cite.section)} · label `{cite.set_id[:8]}`{lock_badge(cite.set_id)}\n"
        )
    lines.append("Relevance-based: it can still answer a question about a real drug that the label does not actually address.")
    return "\n".join(lines), None


def search(query: str, mode: str, top_k: int, viewer: str, missed: list):
    """Returns (markdown, updated per-session list of queries that found nothing, table rows, stats line)."""
    if not query.strip():
        return "Type a question, for example: *Who should not take valacyclovir?*", missed, rows(missed), stats_line()
    mask = MASKS[viewer]
    if mode == "Cited answer":
        text, refused_score = cited_answer(query, mask)
        if refused_score is not None:
            missed = missed + [[time.strftime("%H:%M:%S"), query, mode, viewer, f"relevance {refused_score:.2f}"]]
        return text, missed, rows(missed), stats_line()
    start = time.perf_counter()
    ranking = get_retriever(mode).search(query, int(top_k), allowed=mask)
    ms = (time.perf_counter() - start) * 1000
    if not ranking:
        missed = missed + [[time.strftime("%H:%M:%S"), query, mode, viewer, "no results"]]

    cards = [f"**{len(ranking)} results in {ms:.0f} ms** using *{mode}*\n"]
    for rank, pos in enumerate(ranking, start=1):
        c = chunks[pos]
        snippet = html.escape(" ".join(c.text.split())[:700])
        cards.append(
            f"### {rank}. {html.escape(c.doc_title)}\n"
            f"*{html.escape(c.section_title)}* &nbsp;·&nbsp; label `{c.key[0][:8]}`{lock_badge(c.key[0])}\n\n{snippet}…\n"
        )
    return "\n".join(cards), missed, rows(missed), stats_line()


def rows(missed: list) -> list:
    return missed[::-1]  # newest first


def stats_line() -> str:
    total = STATS["answered"] + STATS["refused"]
    share = f"{STATS['refused'] / total:.0%}" if total else "n/a"
    return f"Cited-answer questions since this app started (all visitors, counts only): {total}, of which {STATS['refused']} refused ({share})."


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
    viewer = gr.Dropdown(list(VIEWERS), value="Admin (every label)", label="Viewing as (synthetic access rules)")
    missed = gr.State([])
    with gr.Tabs():
        with gr.Tab("Results"):
            out = gr.Markdown()
        with gr.Tab("Not found (this session)"):
            gr.Markdown(
                "Questions in this session that returned nothing: cited answers that were refused, or searches with no "
                "readable result. A deployed product would show an admin this list across all users, to find gaps in "
                "the corpus; here it is limited to your own session so visitors do not see each other's queries."
            )
            table = gr.Dataframe(headers=["Time", "Question", "Mode", "Viewing as", "Why"], interactive=False)
            stats = gr.Markdown()
    go = gr.Button("Search", variant="primary")
    inputs, outputs = [query, mode, top_k, viewer, missed], [out, missed, table, stats]
    go.click(search, inputs, outputs)
    query.submit(search, inputs, outputs)
    gr.Examples(EXAMPLES, [query, mode])

if __name__ == "__main__":
    demo.launch()

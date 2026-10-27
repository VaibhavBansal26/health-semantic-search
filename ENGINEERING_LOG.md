# Engineering log

One entry per experiment or decision. Each entry records the setup, the metric, the alternative rejected, and what was learned.

## Template

### YYYY-MM-DD · Stage N · short title
- **Question:**
- **Setup:** (data, model, parameters, commit)
- **Result:** (table or numbers)
- **Decision:**
- **Rejected alternative and why:**
- **Lesson / interview note:**

---

## 2026-10-26 · Stage 5b · harder, paraphrased question set changes the conclusion
- **Question:** are the Stage 5 conclusions an artefact of questions that copy the chunk's wording?
- **Setup:** 58 new questions (`eval/golden_v2.jsonl`) on drugs not used before, written the way a patient or clinician would ask ("Why shouldn't someone on disulfiram take cough syrup?" for a chunk that says alcohol-containing preparations), avoiding the chunk's distinctive phrases. Same retrievers, same corpus.
- **Result (recall@10 / MRR@10):**

| Retriever | v1, 95 easy | v2, 58 harder | pooled, 153 |
|---|---|---|---|
| BM25 + header | 0.86 / 0.552 | 0.76 / 0.416 | 0.82 / 0.501 |
| Dense | 0.93 / 0.551 | 0.93 / 0.613 | 0.93 / 0.575 |
| Hybrid (RRF) | 0.94 / 0.641 | 0.88 / 0.543 | 0.92 / 0.604 |
| Hybrid + rerank | 0.95 / 0.632 | 0.93 / 0.610 | 0.94 / 0.624 |

- **What changed:** on paraphrased questions BM25 drops 10 points and dense search holds at 0.93, so dense is significantly better than BM25 (+0.17 recall@10, +0.20 MRR). Plain hybrid was worse than dense alone on v2 (0.88 vs 0.93), the opposite of the v1 picture.
- **Pooled over 153 questions:** dense, hybrid and hybrid+rerank are all significantly better than BM25 (+0.09 to +0.12 recall@10), but they are statistically indistinguishable from each other (hybrid vs dense: -0.01 [-0.06, +0.03]; rerank vs hybrid: +0.03 [-0.02, +0.07]).
- **Fusion weighting:** giving dense 1.5x, 2x or 3x the BM25 weight changed nothing beyond noise (all within +-0.01 recall@10 of dense). Stopped tuning there: with 153 questions any pick would be fitting noise.
- **Decision:** keep hybrid as the default because exact-term queries (drug names, doses, codes) are where dense search is weakest and this question set under-represents them; keep dense-only and rerank as selectable modes. Revisit with a set that includes exact-match queries.
- **Mistake in my earlier write-up:** the Stage 2-5 entry below said the reranker was "not worth it" on 95 questions. On the harder set it recovered the dense score (0.88 to 0.93) but still is not measurably better than hybrid, and it costs ~2.7 s per query on this machine. The honest statement is "no demonstrated gain for 100x latency", not "no gain".
- **Interview note:** the ranking of methods depends on how hard the questions are. Report results on more than one question set, say how the questions were written, and use paired intervals; a benchmark made of the documents' own words will flatter keyword search.

## 2026-10-25 · Stages 2-5 · evaluation harness, BM25, dense, hybrid, reranker, index benchmark
- **Question:** which retrieval method finds the right label chunk, and what does each step cost?
- **Setup:** 53,275 chunks (959 Rx labels). 95 golden questions written from sampled chunks, one chunk per drug, stratified over 10 section types (`eval/golden.jsonl`). "Chunk" scoring needs the exact source chunk; "section" scoring accepts any chunk of the same section. Dense model `BAAI/bge-small-en-v1.5` (384-dim), reranker `BAAI/bge-reranker-base` over the top 30. "hdr" = drug and section name prepended to each chunk before indexing. Apple M1, 8 GB.
- **Result (chunk-level; 95% bootstrap intervals in `eval/results`, run `python -m hss.eval.stats`):**

| Retriever | recall@1 | recall@10 | recall@50 | MRR@10 | nDCG@10 | p50 latency |
|---|---|---|---|---|---|---|
| BM25 (raw text) | 0.29 | 0.80 | 0.92 | 0.452 | 0.535 | <1 ms |
| BM25 + header | 0.40 | 0.86 | 0.98 | 0.552 | 0.627 | <1 ms |
| Dense (bge-small) | 0.35 | 0.93 | 0.99 | 0.551 | 0.642 | 19 ms |
| Hybrid (RRF) | 0.48 | 0.94 | 0.98 | 0.641 | 0.713 | 20 ms |
| Dense + rerank | 0.47 | 0.95 | 0.98 | 0.655 | 0.728 | 1,635 ms |
| Hybrid + rerank | 0.45 | 0.95 | 0.98 | 0.632 | 0.710 | 2,120 ms |

- **What is real and what is noise (paired bootstrap vs BM25+header):** hybrid is better on recall@10 (+0.07) and MRR (+0.089), both significant. Dense alone is within noise on this set. Reranking adds nothing over hybrid (0.94 vs 0.95 recall@10, MRR slightly lower) at about 100 times the latency, so the reranker is not worth running on this corpus and model pair. With 95 questions, one question is about 1 point.
- **Header text mattered:** prepending drug and section names lifted BM25 recall@10 from 0.80 to 0.86, because many chunks ("Do not use in patients who...") never name the drug.
- **Failure pattern (BM25):** wording gaps. "Who should not take valacyclovir?" vs the label's "contraindicated"; "How does X work in the body?" vs "Mechanism of Action". Dense and hybrid recover these.
- **Index benchmark (53k x 384, 1,000 queries):** exact FAISS flat search takes 1.8 ms and HNSW 0.05-0.2 ms at 98.8-99.6% recall; pgvector HNSW 1.7-2.4 ms at 98.2-99.2% recall (index+table 194 MB vs 82 MB of raw vectors); int8 scalar quantization cuts memory 4x for 0.7 points of recall but was slower than flat in FAISS on this CPU. At 53k vectors an approximate index is unnecessary; it pays off at millions.
- **Limits of this measurement:** the questions were written by reading the source chunk, so they overlap with its vocabulary and favour BM25; true user phrasing will be harder. One relevant chunk per question undercounts when several chunks answer it (see section-level numbers). Benchmark queries are corpus vectors, which flatters recall slightly. 95 questions gives wide intervals.
- **Decision:** ship hybrid (BM25 with header + dense) as the default; keep the reranker as an optional mode, off by default.
- **Rejected alternative:** the cross-encoder reranker as default, because it costs 2 s per query on this hardware for no measurable gain here.
- **Next steps:** write questions in clinician and layperson wording (not copied from chunk text), try a medical embedding model, tune RRF and chunk size on the harness.
- **Interview note:** "How do you know your search is good?" A fixed question set, recall@k and MRR, bootstrap intervals so you do not report noise, and an ablation table so every component justifies its latency. "Is reranking worth it?" Measure it: here it was not.

## 2026-10-25 · Stage 1 · DailyMed ingestion and section-aware chunking
- **Question:** can a drug label be turned into clean, section-bounded chunks reliably?
- **Setup:** DailyMed web services v2, `doctype=34391-3` (human Rx labels, ~52k of ~159k), one label per distinct drug title, 1,000 selected. Parser keyed on LOINC section codes; chunker max 350 approx tokens (chars/4), 50-token overlap in whole paragraphs.
- **Result:** 959 documents loaded (41 empty: medical gases and similar labels with only product data and package artwork), 14,279 sections, 53,275 chunks. Chunk tokens: p50 288, p95 341, max 350. 361 chunks under 20 tokens (0.7%).
- **Mistakes found by inspecting data, not by tests:**
  1. The unfiltered list API returns OTC, devices and cosmetics (first run: 933 docs, 1,681 tiny chunks, mostly OTC boilerplate). Fixed by filtering to Rx at download time.
  2. Max chunk was 359 against a 350 limit: word splitting flushed after the limit, and the blank-line joiner was uncounted. Fixed both; max is now 350.
  3. The SPL document `<title>` is often "Highlights of prescribing information" boilerplate, so the drug name comes from the product data section.
  4. Overlap is whole paragraphs, so an overlap budget smaller than one paragraph silently gives zero overlap.
- **Known issues, deferred:** list introductions ("indicated for the treatment of patients with:") can land in a different chunk from their bullet items; stray "•" paragraphs from list markup; very short sections ("None.") make weak chunks alone. Plan: prepend "Drug · Section" context at embedding time (Stage 4) and revisit list handling after the evaluation harness (Stage 3) shows whether it costs recall.
- **Decision:** keep sections whole in their own table so retrieval can expand a chunk to its parent section.
- **Rejected alternative:** fixed-size chunks across the whole document; they would mix dosing and warnings in one chunk.
- **Interview note:** "How do you chunk?" Structure first (section codes, paragraphs), size limits second, overlap only where a boundary can split a fact. And: inspect the data before trusting the pipeline; the unit tests passed while the corpus was wrong.

## 2026-10-25 · Stage 0 · repository created
- **Decision:** public repo, MIT code license, data licenses tracked separately in LICENSES.md.
- **Decision:** Postgres + pgvector first; revisit a dedicated vector engine in Stage 10 when filtered ANN at scale is measured.

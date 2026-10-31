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

## 2026-10-30 · Stage 7 · brand/generic names: a real terminology gap, fixed with SPL aliases
- **Question:** the failure analysis found no terminology misses, but my questions never used a drug's other name. Does asking about a brand-titled label by its generic name work?
- **Setup:** 26 templated questions (`eval/golden_v4.jsonl`, `hss.eval.brand`): "What is sunvozertinib used for?" / "What are the contraindications of ...?", expecting the indications or contraindications chunk of the brand-titled label (ZEGFROVY). The generic is read from the description and must not appear in any label title. The first 40 generated pairs included 14 generic-named labels in capitals (ISOTRETINOIN, FAMOTIDINE...) and chemical fragments; I removed them by reading the list. The fix uses a separate source: the `genericMedicine` element of each SPL file (`hss.aliases`, all 1,000 labels), and titles become "ZEGFROVY (sunvozertinib)" for BM25, dense embeddings (full re-embed, ~50 min) and the reranker.
- **Result (chunk recall@10 / MRR@10, before then after aliases):**

| Retriever | v4 brand/generic, 26 | v1, 95 | v2, 58 | v3 identifiers, 50 |
|---|---|---|---|---|
| BM25 + header | 0.08 / 0.058 -> 0.50 / 0.318 | 0.86 -> 0.86 | 0.76 -> 0.76 | 0.94 -> 0.94 |
| Dense | 0.08 / 0.077 -> **0.88 / 0.644** | 0.93 -> 0.91 | 0.93 -> 0.93 | 0.06 -> 0.06 |
| Hybrid | 0.08 / 0.077 -> 0.77 / 0.483 | 0.94 -> 0.95 | 0.88 -> 0.86 | 0.60 -> 0.56 |
| Routed hybrid | 0.08 / 0.077 -> 0.77 / 0.483 | 0.94 -> 0.95 | 0.88 -> 0.86 | 0.88 -> 0.84 |
| Hybrid + rerank | 0.08 / 0.077 -> 0.85 / 0.521 | 0.95 -> 0.94 | 0.93 -> 0.95 | 0.94 -> 0.96 |

- **Before:** the right label was already in BM25's top 10 for 26/26 questions, but only the chunks that name the generic matched (description, pharmacology); the indications chunk says only "ZEGFROVY is indicated for...". Dense found the right label in 18/26.
- **What it shows:** dense embeddings use the added name best (0.08 -> 0.88). BM25 gains less (0.50) because every chunk of the label now contains the generic, so the indications chunk competes with its siblings on weak lexical cues. Plain and routed hybrid land below dense alone on this set (0.77 vs 0.88), the same direction as the paraphrased set: when the BM25 list is the weaker one, equal-weight fusion costs recall.
- **No regression elsewhere:** the other three sets move by at most 0.04 recall@10 in either direction, within noise at these sizes (the largest, v3 routed 0.88 -> 0.84, is two questions).
- **Caveats:** the v4 questions are templated, only two question types, 26 questions, so wide intervals; the generic was extracted from the same labels the aliases come from (SPL element vs description text, different fields). The gain depends on labels carrying a `genericMedicine` name, which DailyMed requires. Combination products have several names.
- **Decision:** keep aliases in titles as part of the corpus (the corpus file and the Space need regenerating); dense or reranked search is the right mode for brand/generic questions. Not yet done: ship the aliased corpus and embeddings to the Space, and test fusion weights that favour dense.
- **Interview note:** a synonym gap hides until you ask for it: if every test question uses the corpus's own terms, expansion looks pointless. Put the alias into the indexed text (cheap, no query-time cost) rather than expanding queries, and check the other query types did not get worse.

## 2026-10-30 · Stage 7 (scoping) · what the best retriever still misses
- **Question:** before adding health-specific tuning (synonym expansion, a medical embedding model, fine-tuning), what actually fails?
- **Setup:** adaptive hybrid + rerank on the 153 natural-language questions (v1 + v2); listed every question whose gold chunk is not in the top 10 and compared it with the gold chunk.
- **Result:** 8 misses of 153 (recall@10 0.95). Causes: in 4 the right drug and section are in the top 10 but a sibling chunk outranks the gold one (e.g. Xeljanz dose for hepatic impairment: the section is rank 1, the specific chunk is not found; colesevelam/olmesartan, metformin pregnancy, citalopram suicidality table); in 2 the answer is plausibly contained in several chunks (pomalidomide pregnancy, buprenorphine serious adverse reactions); 1 is a vocabulary issue (celecoxib "chemical class" vs a chunk titled "Generic drug / DESCRIPTION": the drug title is the generic label name); 1 is a long table caption (citalopram).
- **What it means:** none of the misses is a synonym or terminology gap, which is what query expansion and a medical embedding model would fix. The remaining error is mostly the evaluation design (one gold chunk per question while several chunks legitimately answer) and chunk granularity. At this corpus size and question mix, health-specific tuning has almost no room to show a gain, and an experiment would be measuring noise.
- **Decision:** do not spend the 1-hour re-embedding on a medical model or build RxNorm/MeSH expansion yet. Revisit if a larger corpus or real user queries show terminology failures (brand vs generic names, abbreviations).
- **Interview note:** look at the failures before choosing the next improvement. If errors are labelling or granularity problems, a better model is the wrong fix.

## 2026-10-30 · Stage 6 · extractive cited answers and a refusal threshold
- **Question:** can the system return only what the labels say, with a source, and say "no supported answer" otherwise?
- **Setup:** `hss.answer.Answerer`: routed hybrid + cross-encoder rerank, then the best 2 sentences (scored by the same cross-encoder) from the top 3 chunks, returned verbatim with drug, section and set ID. No generation, so no hallucinated text; the failure mode is returning a true but irrelevant sentence. Refuse when the top reranker score (a 0-1 sigmoid) is below a threshold. Threshold chosen to maximize balanced accuracy on v1 (95 answerable) + unanswerable set A (15); tested on v2 (58) + set B (15); set C (15 hard) written afterwards and not used for choosing anything. Questions in `eval/unanswerable.json`: A and B mix made-up drug names, off-topic questions and medical-adjacent ones (cost, pharmacies, personal diagnosis); C asks about drugs that are in the corpus for things a label does not say (dose for dogs, cost, "does it cure the common cold").
- **Result (threshold 0.87):**

| Set | Outcome |
|---|---|
| v1 answerable (calibration) | 95/95 answered |
| A unanswerable (calibration) | 15/15 refused |
| v2 answerable (held out) | 53/58 answered (0.91) |
| B unanswerable (held out) | 15/15 refused |
| C hard unanswerable | 12/15 refused (0.80) |

- **What this means:** on easy unanswerable questions the top score is near 0 (max 0.68) while answerable ones are mostly near 1, so the split is clean. The hard set is the honest number: "Does azithromycin cure the common cold?" (0.99), "dose of amlodipine for dogs" (0.98) and "dose of levetiracetam for cats" (0.95) were answered, because the retrieved chunks are on the same drug and mention nearby things (infections, animal toxicology studies). A relevance score cannot tell "on the right topic" from "answers the question".
- **Weakness of the threshold:** 0.87 is the lowest v1 score, i.e. it sits at the edge of the calibration data; the held-out answerable set already lost 9% of questions at that threshold. Lowering it recovers those but admits more of set C. With 15-question unanswerable sets the choice is fragile and the intervals are wide.
- **Spot check:** "Is tolterodine safe to take while pregnant?" cites the pregnancy section, but the second sentence returned is about breastfeeding. Sentence picking returns a fixed count, so the second sentence is sometimes only loosely relevant.
- **Not done:** faithfulness of generated answers was not measured because nothing is generated; a support check (does the sentence entail the answer) would need an NLI model or an LLM judge. Citations are to the chunk the sentence came from, so every returned sentence is trivially supported by its own source.
- **Decision:** ship extractive answers with the 0.87 refusal rule in the demo, labelled as relevance-based; do not claim it refuses unsupported questions about in-corpus drugs.
- **Rejected alternative:** an LLM-written answer; no API key in this environment, and extractive output cannot invent text. Revisit with a measured faithfulness check.
- **Interview note:** hallucination has two layers: invented text (avoided by extraction) and answering from irrelevant-but-related evidence (not avoided by a relevance threshold). Evaluate refusal on questions that are close to the corpus, not only on off-topic ones.

## 2026-10-28 · Stage 5d · query-dependent fusion weights
- **Question:** does up-weighting BM25 when the query contains an identifier fix plain hybrid's collapse on identifier lookups (Stage 5c) without hurting the other sets?
- **Setup:** `AdaptiveHybridRetriever`: RRF of BM25 and dense, BM25 weight 4 (dense 1) if the query matches `is_code_like` (hyphenated digit groups such as NDC/CAS, or 3+ character tokens mixing letters and digits), else equal weights. The weight 4 and the detector were chosen once, before running, and not tuned. Evaluated on all three sets, with and without the reranker.
- **Result (recall@10 / MRR@10):**

| Retriever | v1, 95 | v2, 58 | v3 identifiers, 50 | all 203 |
|---|---|---|---|---|
| BM25 + header | 0.86 / 0.552 | 0.76 / 0.416 | 0.94 / 0.860 | 0.85 / 0.589 |
| Dense | 0.93 / 0.551 | 0.93 / 0.613 | 0.06 / 0.045 | 0.71 / 0.444 |
| Hybrid (equal weights) | 0.94 / 0.641 | 0.88 / 0.543 | 0.60 / 0.280 | 0.84 / 0.524 |
| Hybrid, adaptive | 0.94 / 0.641 | 0.88 / 0.543 | 0.82 / 0.489 | 0.89 / 0.576 |
| Hybrid + rerank | 0.95 / 0.632 | 0.93 / 0.610 | 0.94 / 0.886 | 0.94 / 0.688 |
| Adaptive + rerank | 0.96 / 0.633 | 0.93 / 0.610 | 0.96 / 0.906 | 0.95 / 0.694 |

- **What worked:** on identifier queries adaptive weighting lifts hybrid from 0.60 to 0.82 recall@10 (MRR 0.280 to 0.489), and on the other two sets it is identical to plain hybrid (the detector rarely fires on natural-language questions).
- **What did not:** it is still significantly worse than BM25 alone on identifiers (-0.12 recall@10 [-0.22, -0.04]). Fusion with a dense list that is pure noise costs something even at weight 4. Skipping the dense list entirely for code-like queries would likely close the gap; not tried.
- **Reranker:** adaptive + rerank is nominally best everywhere (0.95 pooled), but its edge over plain hybrid + rerank is within noise (v3: +0.02 [0.00, +0.06]). The reranker, not the fusion weights, is what carries identifier queries.
- **Caveats:** the pooled column mixes the sets 95/58/50, so it depends on how many identifier questions I wrote; read the per-set columns. The v3 questions are templated and the detector was designed knowing their shape (NDC, CAS, imprint), so the v3 gain is optimistic. All-letters imprints (e.g. CEP) are not detected.
- **Decision:** make adaptive hybrid the default fast mode (never worse than equal-weight hybrid here, much better on identifiers); keep the reranker optional.
- **Interview note:** query routing beats one global setting. Sparse and dense retrieval fail on different query types, so detect the type cheaply and weight accordingly; then check that you did not break the other types.
- **Follow-up (routed, same day):** `skip_dense=True` returns the BM25 list alone for code-like queries. Identifier set: 0.88 recall@10 / 0.766 MRR (adaptive weights 0.82 / 0.489; BM25 alone 0.94 / 0.860); v1 and v2 unchanged (0.94, 0.88). Versus BM25 alone the recall gap shrinks to -0.06 [-0.14, 0.00], within noise, but MRR is still lower (-0.094, significant). The remaining gap is the questions the detector misses (all-letter imprints such as CEP and WARFARIN go through hybrid); routed and adaptive are otherwise equal on natural-language questions. Better detection (e.g. a vocabulary check against the corpus) is the next lever, not the weights.

## 2026-10-28 · Stage 5c · exact-match identifier lookups reverse the ranking
- **Question:** Stage 5b left open whether hybrid earns its place on queries dense search is worst at: literal identifiers.
- **Setup:** 50 templated questions (`eval/golden_v3.jsonl`, built by `hss.eval.exact`): 25 NDC codes ("What product is NDC 61314-647-25?"), 9 CAS numbers, 16 tablet imprints. Each identifier occurs in exactly one chunk, one question per drug. Nothing is paraphrased. Run from the exported corpus file (`--corpus`), same embeddings.
- **Result (chunk recall@10 / MRR@10):**

| Retriever | all 50 | NDC (25) | CAS (9) | imprint (16) |
|---|---|---|---|---|
| BM25 + header | 0.94 / 0.860 | 25/25 | 9/9 | 13/16 |
| Dense | 0.06 / 0.045 | 0/25 | 0/9 | 3/16 |
| Hybrid (RRF) | 0.60 / 0.280 | 14/25 | 7/9 | 9/16 |
| Hybrid + rerank | 0.94 / 0.886 | 24/25 | 9/9 | 14/16 |

- **What it shows:** the Stage 5b ranking flips. Dense embeddings cannot look up an identifier (-0.88 recall@10 vs BM25, significant). Plain hybrid is also significantly worse than BM25 alone (-0.34): equal-weight RRF lets the dense list, which has no signal here, push the one correct BM25 hit down. The reranker rescues it (candidates are still in hybrid's top 30: recall@50 is 0.96) and ties BM25.
- **Net across the three sets:** no single method wins everywhere. BM25 is best on identifiers (0.94) and worst on paraphrases (0.76); dense is the reverse (0.06 vs 0.93). Only hybrid+rerank is at or near the top on all three, at ~2 s per query on this machine.
- **Misses:** 3 imprint questions failed for BM25 (a newline inside the token, "A109" with a double space, and the imprint "WARFARIN" which tokenizes like the drug name). Two of these come from my regex capturing messy text, not from retrieval.
- **Limits:** templated, so easier and more uniform than real user phrasing; 50 questions, wide intervals; identifiers are the extreme case, not a typical query mix.
- **Decision:** keep hybrid+rerank as the quality mode and plain hybrid as the fast default for now, but plain equal-weight RRF is not good enough: next experiment is query-dependent fusion (up-weight BM25 when the query contains a code-like token) and measure it on all three sets. Do not report "hybrid is best" without saying it loses to BM25 on identifier queries.
- **Interview note:** hybrid search is a hedge, not a free win. Test it on the query type each side is bad at; RRF with equal weights can be worse than the better single retriever when one list is noise.

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

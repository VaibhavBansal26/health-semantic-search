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

## 2026-11-01 · Stage 10 (part 2) · load test of the real search stack
- **Question:** how many queries per second does the real stack serve on one machine, and what happens to latency as load rises?
- **Setup (`hss.loadtest`):** closed loop, N workers each sending the next query as soon as the last returns, in one process on an 8-core Apple laptop (GPU via MPS), real 53,275-chunk corpus, the 203 evaluation questions in fixed shuffled order. 400 requests per level for the routed hybrid (BM25 + dense), 100 per level for hybrid + cross-encoder rerank of the top 30. In-process, so no HTTP or network time.
- **Result:**

| Workers | Hybrid QPS | Hybrid p50 / p95 / p99 (ms) | Hybrid + rerank QPS | Rerank p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | 63.9 | 19 / 21 / 25 | 0.6 | 1,773 / 2,437 |
| 2 | 73.7 | 34 / 36 / 39 | 0.5 | 3,568 / 5,833 |
| 4 | 72.0 | 67 / 78 / 110 | 0.5 | 7,837 / 11,367 |
| 8 | 71.8 | 135 / 151 / 228 | 0.5 | 16,588 / 18,621 |

  Peak memory 0.8 GB (hybrid) and 1.1 GB (with reranker).
- **Reading it:** throughput does not scale with workers for either stack; it stays at about 72 QPS (hybrid) and 0.5 QPS (rerank) while latency grows in proportion to the number of waiting requests. That is a queue in front of one resource: the model calls. Hybrid at one worker is 19 ms, so a single user sees a fast search, but a rerank costs about 1.8 s of exclusive GPU time per query, so the reranked mode serves about one query every two seconds on this machine however many requests arrive. To serve, say, 20 QPS with reranking at this depth would take roughly 40 such machines, or a cheaper reranker, a smaller candidate depth, or batching across requests; I have not tried any of those.
- **Bug found by the load test:** with two or more threads, the reranker on the Apple GPU aborted the whole process (a Metal "command encoder is already encoding" assertion). Fixed with one process-wide lock around model inference (`MODEL_LOCK`), including the dense query encoder and the sentence scoring in cited answers. The lock costs hybrid throughput: the same test before the lock reached 61.5 / 68.1 / 84.9 / 89.5 QPS at 1 / 2 / 4 / 8 workers (hybrid only, one earlier run), against 63.9 / 73.7 / 72.0 / 71.8 with it. The earlier run is a single observation on a machine doing other work, so I would not read the 18% gap at 8 workers as exact.
- **Limits:** one machine shared with other tasks, one run per configuration; the reranker levels have only 100 requests, so p99 there is nearly the maximum; closed-loop load hides the latency a real arrival process would see; query encoding and reranking use the GPU, so a CPU-only server (like the free Space tier) would be slower on the model steps, which I did not measure; no filtered (masked) or cited-answer load, no Postgres in the path, no HTTP.
- **Decision:** keep reranking an opt-in mode, not the default path; the default (hybrid) fits a many-users-per-machine budget, the reranker does not.
- **Rejected alternative:** per-thread model copies to avoid the lock: memory cost and no more GPU throughput, since the GPU runs one batch at a time.
- **Interview note:** "What's your throughput?" Say the number and where it flattens: about 72 QPS for hybrid, 0.5 for rerank, both limited by model inference on one device, and that the load test found a crash that unit tests and single-user use never would.

## 2026-11-01 · Stage 10 (part 1) · 10 million vectors on an 8.6 GB laptop
- **Question:** what does vector search at 10M vectors cost in memory, latency and recall when the exact index does not fit in RAM?
- **Setup (`hss.scale`):** 10M vectors of 384 dimensions. They are SYNTHETIC: each is a real chunk embedding plus Gaussian noise (sigma 0.05), re-normalised, regenerated from a seed so exact search can be streamed over all 10M. This data is more clustered than real text (about 187 noisy copies per real vector), so treat recall as indicative and the memory and build numbers as the solid ones. Index: IVF with 4096 cells (HNSW over the cells for cheap adds) plus product quantization at 48 or 96 bytes per vector. 300 queries; ground truth is exact inner-product search over all 10M.
- **Memory:** exact fp32 would need 15.4 GB (fp16 7.7 GB, int8 3.8 GB): none fits comfortably in 8.6 GB. The PQ indexes are 0.57 GB (48 B) and 1.05 GB (96 B) on disk. Peak process memory was 2.6-3.0 GB during the build (which holds a 500k-vector block at a time) and 3.2-4.2 GB over the whole run including the exact-search pass. Training took 85-163 s on 500k vectors, adding 10M took 283-333 s.
- **Recall@10 (strict id overlap with the exact top 10 | "near": returned items scoring within 0.01 of the exact 10th-best):**

| Code size | nprobe 4 | nprobe 16 | nprobe 64 |
|---|---|---|---|
| 48 bytes | 0.31 \| 0.49 | 0.32 \| 0.49 | 0.32 \| 0.49 |
| 96 bytes | 0.56 \| 0.80 | 0.58 \| 0.82 | 0.58 \| 0.82 |

  Recall stops improving after nprobe 4: probing more cells does not help, so the loss is from compression, not from missing the right cells. On this clustered data, where many vectors are near-ties, 48 bytes is too lossy and 96 bytes is still short of what I would ship. The usual fix is to re-score the PQ candidates with the full-precision vectors; that needs the vectors in memory or on fast disk (15 GB fp32), so I did not test it and cannot say how much it would recover.
- **Latency (single thread, one query at a time):** p50 0.3-0.4 ms at nprobe 4, rising to 5.7-7.2 ms at nprobe 16-64 (p95 up to 17-21 ms). Four workers reached about 5,700-9,600 QPS at nprobe 4 (p95 1 ms) and 600-1,000 QPS at nprobe 64 (p95 5-9 ms); eight workers added little on top, consistent with 8 cores shared with the OS.
- **Measurement problems, said plainly:** the first pass of the concurrency test was wrong (OpenMP thread count is per calling thread, so workers oversubscribed); I fixed it and reran both indexes. The one-worker QPS (140-490) is still far below what the single-thread latencies imply and well below the four-worker figure; I could not explain it, so I do not report one-worker QPS as a result. The machine was shared with other work during the runs. Latency at nprobe 16 vs 4 jumps about 15x for 4x the probes, which I have not investigated either. One of the first 48-byte attempts died silently after building the index (cause unknown), and the numbers above come from clean reruns.
- **Not done:** the load test on the real search stack (BM25 + dense + rerank over the actual 53k-chunk corpus), a refine step with full vectors, a real 10M corpus, filtered (permission-masked) search at this scale, Postgres/pgvector at 10M.
- **Decision (provisional):** at this scale a compressed IVF index fits in about 1 GB, but plain PQ alone does not give good enough recall; a refine stage is the next thing to measure before choosing it.
- **Rejected alternative:** HNSW over full vectors at 10M: the graph plus vectors would need well over 15 GB.
- **Interview note:** "How does it scale to 10M?" Do the arithmetic first (10M x 384 x 4 bytes = 15 GB), then compress and measure what the compression costs, separating the recall lost to probing too few cells from the recall lost to quantization. Say which numbers come from synthetic data.

## 2026-10-31 · Stage 8 · incremental sync that survives being killed (ClinicalTrials.gov)
- **Question:** can a source be pulled incrementally so that killing the process at any moment leaves no gaps and no duplicates?
- **Design:** a source yields pages ordered by update date from a cursor (`hss.sync.core`). Each page's documents and the cursor move are written in one Postgres transaction, so a crash leaves either all of a page with the cursor past it, or none of it with the cursor where it was. The range is inclusive, so the cursor's own day is read again after a restart; a record seen twice is skipped by content hash, and a changed one is replaced (delete + insert, same transaction). `UNIQUE (source, set_id)` rules out duplicates at the database level. Connector: ClinicalTrials.gov API v2, `filter.advanced=AREA[LastUpdatePostDate]RANGE[cursor,MAX]`, sorted by update date ascending, three sections per study.
- **Test (`python -m hss.sync.crashtest`, needs Postgres):** fake source of 300 records over 30 days, pages of 25, so pages cross day edges. (1) 7 hard exits (`os._exit`) inside page transactions, on page edges and just after; (2) 8 random SIGKILLs from outside, then a clean run; (3) 40 records revised at the source (date moves forward), 5 more SIGKILLs, clean run. After each: same id set as the source, one row per id, content hash and chunk count equal to a clean load, no orphan sections or chunks, cursor at the newest date. All pass.
- **The test can fail:** I moved the cursor commit before the page write on purpose. It then reported missing ids (CT00000-CT00004 and more) and 12 stale documents. Restored, it passes again.
- **Live check:** `--since 2026-10-29` pulled 299 studies in 3 pages; a second run was SIGKILLed mid-run (database still 299 documents, no partial rows), and a rerun resumed and ended at 799 documents, 799 distinct ids, 4,404 chunks. The DailyMed corpus is unchanged at 53,275 chunks (`load_chunks` now filters on source).
- **Limit that showed up live:** ClinicalTrials.gov exposes only a date, and about 5,000 studies were updated in the last week, so one busy day spans many pages. The cursor only moves to the newest date in a committed page, so after a kill the run re-reads that whole day from its start (299 unchanged skips in the live rerun). Correct but wasteful; storing the API page token would fix it, but tokens are opaque and may expire, so I did not.
- **Not done:** openFDA (labels overlap DailyMed, so it needs a dedup rule first); deletions and withdrawn records (the CT.gov update feed does not mark them); embedding the new documents (the retrievers still index DailyMed only); scheduling; rate-limit handling beyond retry with backoff; the crash test runs against a fake source, and only the one live kill was against the real API.
- **Decision:** transaction-bound cursor plus content hash, instead of a separate job table with per-record status.
- **Rejected alternative:** advancing the cursor after the whole run finishes. It is also correct, but a late failure repeats everything, and with thousands of updates per day that is the common case.
- **Interview note:** "How do you make sync reliable?" Make the commit point atomic: data and cursor in one transaction. Re-read from the cursor and make writes idempotent so repeats are harmless. Then prove it by killing the process at random and diffing against the source, and show that the test fails when you break the guarantee.

## 2026-10-31 · Stage 9 · permission-aware retrieval: filter before ranking, never after
- **Question:** can retrieval enforce document-level access control with zero leakage, and does it cost recall or latency?
- **Setup:** DailyMed labels are public, so access rules are synthetic: a hash of `set_id` makes 48% of labels public and restricts the rest to one or two of four groups (`hss/acl.py`). Four users: anonymous (sees 47% of chunks), one group (65%), two groups (80%), admin (100%). Every retriever takes an `allowed` mask over chunk positions: BM25 via `weight_mask`, dense by masking scores, hybrid and reranker pass it to their first stages. 203 pooled questions (v1, v2, v3). Four strategies compared: unfiltered, post-filter top 10, post-filter top 50 (keep 10), pre-filter. Recall counts only questions whose answer the user may read.
- **Result, leakage:** unfiltered search would have shown unreadable chunks for 66-96 of the questions per restricted user (anonymous: 291-326 leaked chunks per retriever), and returned the unreadable gold chunk for 87 of 104 hidden-answer questions (BM25, anonymous). Pre-filtering leaked 0 chunks and returned 0 hidden gold chunks for every retriever and user. Post-filtering also leaked 0, by construction.
- **Result, completeness and recall (recall@10 on readable questions, anonymous user):**

| Retriever | post-10 (results/query) | post-50 | pre-filter |
|---|---|---|---|
| BM25 + header | 0.87 (6.7) | 0.89 | 0.89 |
| Dense | 0.71 (7.0) | 0.73 | 0.73 |
| Hybrid routed | 0.95 (7.1) | 0.98 | 0.94 |
| Adaptive hybrid + rerank | 0.98 (7.0) | 1.00 | 1.00 |

  Post-filtering the top 10 hands a restricted user short lists (about 7 of 10 for anonymous) and loses a little recall. Over-fetching 50 and then filtering recovers nearly all of it here. Pre-filter and post-50 differ by 0-4 points, in both directions (routed hybrid is 0.94 vs 0.98 for anonymous), which is within what 99 questions can resolve, so I do not claim pre-filtering wins on recall on this corpus.
- **Latency:** pre-filtering added no measurable time for BM25 and dense (1 ms and 18 ms in both cases). The reranker timings (2.5-8.7 s) are not usable: the machine was busy and sleeping during that run and the unfiltered and pre-filter times swing in both directions, so I do not report them.
- **Narrow tenant (measured, `hss.eval.acl_narrow`):** a user granted 40 labels (5.1% of chunks), 40 questions about those labels plus 40 about labels they cannot read. Recall@10 on the readable questions, then results returned for the unreadable-topic questions (40 asked, count of empty lists):

| Retriever | post-10 | post-50 | post-200 | pre-filter | unreadable topic: empty lists post-50 / post-200 / pre |
|---|---|---|---|---|---|
| BM25 + header | 0.85 | 0.93 | 0.95 | 0.95 | 17 / 6 / 0 |
| Dense | 0.78 | 0.80 | 0.80 | 0.80 | 14 / 5 / 0 |
| Hybrid routed | 0.97 | 0.97 | 1.00 | 1.00 | 17 / 10 / 0 |

  Over-fetching needs a fetch depth that grows as the readable share shrinks: top 10 shortens lists to under 5 results, top 50 still returns an empty list for 14-17 of 40 unreadable-topic questions, and even top 200 leaves 5-10 empty. Pre-filtering returns a full list every time and matches or beats the best post-filter on recall (equal to post-200, a few points above post-10/post-50 for BM25 and hybrid). On the readable questions the gap is small because those questions are about the readable labels, so their answers rank high anyway; the failure shows up for everything else.
- **Caveat that follows from this:** a full list for a question the user has no access to means ten irrelevant readable chunks. The cited-answer refusal threshold (reranker score) is what turns that into "not found"; the retrieval layer alone cannot tell. That path is only unit-tested under a mask (stub reranker), not measured.
- **Cache isolation (`hss.cache`, tests):** the result cache keys on query, k and a digest of the access mask, so a user is never served another user's cached results, while users with identical access share entries. Tests show an admin's cached result is not returned to an anonymous user for the same query. A cache keyed on the query alone would fail that test; I did not build the broken version, the test just encodes the requirement.
- **Why pre-filter anyway:** unreadable text never reaches fusion, the reranker or the answer step, and lists stay full at any readable share (the narrow-tenant run above is the measured case; the 47-100% users cannot show it).
- **Gotcha found while building:** BM25 with a weight mask still pads the list with zero-score masked chunks when a query has few matches, so results are filtered again at the boundary (tested in `tests/test_acl.py`).
- **Limits:** synthetic, document-level rules only; four users plus one narrow tenant with 40 questions; the narrow-tenant reranker was not run (slow); no per-section rules, revocation, or index-per-tenant design; the cited-answer path is unit-tested under a mask but not measured; the demo does not expose it yet; cache tests are in-process only, with no invalidation when permissions change.
- **Decision:** enforce access as a mask applied inside every first-stage search, plus a final check on returned positions.
- **Rejected alternative:** post-filtering the top-k, because it returns short lists and degrades as the readable share shrinks; also because unreadable chunks would flow through ranking and answer scoring.
- **Next steps:** evaluate cited answers and refusal under a narrow mask; cache invalidation on permission change; decide how the mask is derived from real identity (groups from an identity provider) and kept fresh.
- **Interview note:** "How do you do access control in search?" Enforce it in the query, not on the results: a filter mask in each retriever so ranking, reranking and answers never see unreadable text. Post-filtering leaks nothing but gives short or empty lists, and a hidden document can still influence scores. Then say honestly what you measured: zero leakage across 203 questions and four users, and that the recall advantage did not show at this corpus size.

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
- **Follow-up, dense-favouring fusion weights on the aliased corpus (dense 1.5x / 2x / 3x):** brand/generic recall@10 rises only from 0.77 (equal weights) to 0.77 / 0.77 / 0.81, still below dense alone (0.88); v1 MRR falls from 0.646 to 0.609 / 0.592 / 0.590; v2 recall@10 moves +0.03 to +0.05 (0.86 to 0.90-0.91); on identifiers recall collapses to 0.06-0.08 without routing. No weight is better everywhere, so equal weights plus routing stay, same conclusion as Stage 5b. Query-type routing, not a global weight, is the lever.
- **Deployed:** the Space now serves the aliased corpus and embeddings (`hss.export --aliases --corpus F`). The un-aliased corpus is kept at `data/processed/corpus.jsonl.gz` (gitignored). Note `deploy/space/data/corpus.jsonl.gz` is now the aliased one; evaluation commands that pass `--corpus` with it and `--aliases` are idempotent (titles that already carry the generic are not changed).

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

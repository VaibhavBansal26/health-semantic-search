# Permission-Aware, Cited Retrieval over Public Drug Labels: Measurements, Negative Results and Limits

Vaibhav Bansal. [Affiliation and contact: to be added by the author before submission.]

Draft status: workshop-paper draft written from the project's engineering log (`ENGINEERING_LOG.md`) and summary (`docs/WRITEUP.md`); every number is taken from those files or from the saved result files under `eval/results/`. Two things are open and marked in the text: the results on real user questions (Section 6.12; none have been collected yet), and the author line. References were checked against Crossref and arXiv (see the note above the list); page numbers should be re-checked before submission.

## Abstract

We describe a search and cited-answer system over US prescription drug labels and report what we measured while building it. The system combines BM25, a small dense embedding model and reciprocal rank fusion, routes queries that contain identifiers (NDC and CAS numbers, tablet imprints) to keyword search, optionally reranks with a cross-encoder, and returns extractive answers made of verbatim label sentences with citations, or refuses. Access control is applied as a mask inside every retriever, before ranking. Evaluation uses five small, model-written question sets (v1 to v5, 348 questions in total) over one corpus of 959 labels (53,275 chunks), with paired bootstrap intervals. The main findings are these. No single retriever wins on all question types: BM25 leads on identifier lookups (recall@10 0.94 against 0.06 for dense), while dense retrieval leads on paraphrased and naturally worded questions. Adding a brand-name label's generic name to its indexed title raised dense recall@10 on brand/generic questions from 0.08 to 0.88. Query expansion from RxNorm and MeSH did not help, and a dense-first routing rule failed its pre-stated acceptance test. Masked retrieval returned no unreadable chunks across 203 questions and four users, and returned full result lists where post-filtering returned empty ones for a user who can read 5% of the corpus. All 591 cited sentences in one run were verbatim copies of the chunk they cite. We also report a 10-million-vector index experiment on synthetic vectors and a single-machine load test. All questions are small-scale and written by a model or by the author; there are no real user queries yet, so the results should be read as engineering evidence on one corpus, not as a benchmark of real-world quality. A real-query evaluation protocol is specified and not yet run.

## 1 Introduction

Clinicians, pharmacists and patients ask questions whose answers sit in drug labels: contraindications, dosing in renal impairment, interactions, pregnancy. A deployed search system over such text has to do four things that are usually studied separately. It has to retrieve well across very different query types (a tablet imprint and a paraphrased safety question are both legitimate queries). It has to show where an answer came from and decline when nothing supports one. It has to respect who may read what, in the search itself and not only in the user interface. And it has to stay current as the sources change without serving stale or half-written data.

This paper reports a staged build of such a system, with each design decision tied to an experiment recorded in an engineering log. The emphasis is on measurement discipline: question sets are versioned, differences are tested with paired bootstrap intervals, some acceptance rules were written down before the runs, and negative results are kept.

Contributions:

- A routed hybrid retrieval design (BM25, dense, reciprocal rank fusion, identifier routing, optional reranker) with per-query-type measurements showing where each component helps and where it hurts, including two negative results (RxNorm/MeSH query expansion; dense-first routing as a new default).
- A brand/generic finding: a terminology gap that only appeared once questions used a label's generic name, and a cheap index-side fix (alias in the title) with its effect on each retriever.
- A permission-aware retrieval design (filter before ranking, mask-keyed cache, signed tokens with RS256 and revocation) with a leakage test and a narrow-tenant comparison against post-filtering.
- Extractive cited answers with a refusal threshold, evaluated for verbatim fidelity, attribution to the source label, and refusal behaviour on unanswerable questions, including one set written after the threshold was fixed.
- Kill-safe incremental sync and versioned index publishing, tested by killing the process at random points.
- Scale and serving measurements: a 10M-vector index on synthetic vectors on a laptop, and a closed-loop load test of the real stack.
- A pooled-labeling protocol and tooling for a real-query evaluation (v6), specified here and not yet run.

The evaluation is small and the corpus is one source type. Section 9 lists the limits, and the abstract and conclusions should be read with them.

## 2 Related work

Lexical retrieval. BM25 remains a strong baseline for term-matching queries [Robertson and Zaragoza, 2009]. Our BM25 variant prepends the drug and section names to each chunk before indexing.

Dense retrieval. Dual-encoder dense retrieval [Karpukhin et al., 2020] embeds queries and passages in one space. We use a small general-purpose embedding model from the BGE family [Xiao et al., 2023]. The BEIR benchmark [Thakur et al., 2021] showed that the ranking of lexical and dense methods depends on the dataset, which is consistent with our per-question-type findings; we do not claim to reproduce its results.

Fusion and reranking. Reciprocal rank fusion [Cormack, Clarke and Buttcher, 2009] merges ranked lists using ranks only, so lexical and cosine scores need no calibration. Cross-encoder reranking with BERT-style models [Nogueira and Cho, 2019] rescored a first-stage candidate list. We use a BGE cross-encoder over the top candidates.

Approximate nearest-neighbor search. Graph indexes [Malkov and Yashunin, 2018] and inverted-file and product-quantized indexes in FAISS [Johnson, Douze and Jegou, 2019] are the standard tools for large vector collections. Our scale experiment uses the latter.

Retrieval-augmented generation. RAG [Lewis et al., 2020] conditions generation on retrieved passages. Our answers are extractive: no text is generated, so the question of generative faithfulness does not arise, and we measure attribution accuracy instead.

Evaluation methodology. We use the bootstrap [Efron, 1979] for confidence intervals and pooling [Sparck Jones and van Rijsbergen, 1976] for the planned real-query labeling.

Biomedical retrieval is usually evaluated on collections such as TREC-COVID (Voorhees et al., 2021) and BioASQ (Tsatsaronis et al., 2015). We did not run on either; a comparison on one of them is the most useful external check this work lacks. Query expansion with controlled vocabularies such as UMLS (Bodenreider, 2004) and MeSH is an established idea; our pilot used RxNorm and MeSH and found no gain (Section 6.6), and we did not compare against published expansion methods. For filtered vector search, graph indexes with filter support such as Filtered-DiskANN (Gollapudi et al., 2023) address the same problem as our access mask at much larger scale; we apply a boolean mask inside brute-force and BM25 search and compare it only against post-filtering (Section 6.8). The dense model runs through the sentence-transformers library (Reimers and Gurevych, 2019). We make no claim about related work on drug-label question answering or on medical embedding models beyond the one model we compared, because we did not find references we could verify.

## 3 Data and licensing

Corpus. The main corpus is 959 human prescription drug labels from DailyMed (US National Library of Medicine), selected as one label per distinct drug title from about 52,000 Rx labels (`doctype=34391-3`) among about 159,000 in the list service; 1,000 were selected and 959 loaded, because 41 were empty labels (medical gases and similar, with only product data and package artwork). The parser is keyed on LOINC section codes and yields 14,279 sections and 53,275 chunks. A first run with an unfiltered list gave OTC, device and cosmetic labels (933 documents, 1,681 tiny chunks, mostly boilerplate), which is why the filter to Rx is applied at download time.

Additional sources. ClinicalTrials.gov (799 studies, 4,404 chunks in one live run) and openFDA drug labels (296 labels in a live run; 4,684 chunks embedded in the first index build) are connected through the sync machinery of Section 4.9. Trials were embedded for an experiment but are excluded from the deployed index because no gain on drug-label search was measured. The search index source list is DailyMed plus openFDA.

Licensing. Every source is recorded in `LICENSES.md` before ingestion, with commercial-use and redistribution status. The sources actually used are US government works or public domain: DailyMed (public domain; per-label third-party content should be verified), ClinicalTrials.gov (public domain under NLM terms of use) and openFDA (public domain, CC0, with a not-for-clinical-use disclaimer). Other sources in the ledger (CMS coverage documents with AMA CPT notices, PubMed abstracts, PubMed Central open-access subsets, RxNorm and UMLS/SNOMED CT under the UMLS license, MeSH) are listed with "check" status and are not ingested. RxNorm and MeSH were queried through public APIs for the expansion experiment only (Section 6.6). The ledger states that its status values must be confirmed against each source's current terms and is not legal advice.

Synthetic parts. The access rules, tenants, the narrow-user grants and the 10-million-vector collection are synthetic. Labels are public; the rules are generated for testing.

## 4 System

### 4.1 Chunking

Labels are parsed by LOINC section code. Chunks hold at most about 350 tokens (estimated as characters divided by four) and overlap by 50 tokens in whole paragraphs, so an overlap budget smaller than one paragraph gives no overlap. Chunk token counts have median 288, 95th percentile 341 and maximum 350; 361 chunks (0.7%) are under 20 tokens. Sections are stored whole in their own table so that a chunk can be expanded to its parent section. Known weaknesses recorded in the log: list introductions can land in a different chunk from their items, stray bullet paragraphs, and weak chunks from very short sections.

### 4.2 Keyword retrieval

BM25 over chunk text. In the main variant the drug name and section name are prepended to each chunk ("header"), because many chunks (for example "Do not use in patients who...") never name the drug. The header lifted BM25 recall@10 on v1 from 0.80 to 0.86.

### 4.3 Dense retrieval

Chunks are embedded with `BAAI/bge-small-en-v1.5` (384 dimensions), with the same header text, and queries use the model's retrieval prompt. Search is exact inner product over the matrix; an approximate index is unnecessary at this size (Section 7.1).

### 4.4 Fusion and identifier routing

Rankings are fused with reciprocal rank fusion, score(d) = sum over lists of weight / (60 + rank), over the top 50 of each list, with equal weights by default. A detector decides whether the query contains an identifier. The first detector fires on hyphenated digit groups (NDC, CAS) and on tokens that mix letters and digits of three or more characters. A wider detector adds a phrase rule ("debossed/imprinted/engraved/stamped/marked with X", "imprint X") and a bare-number rule (two or more digits, not followed by a unit, with ages, ranges, years and "Ammonia N 13" excluded). Routing sends a query that fires the detector to the keyword list alone; other queries use equal-weight fusion. The routing variant used throughout the later results is `hybrid_routed_wide`.

### 4.5 Reranker

A cross-encoder (`BAAI/bge-reranker-base`, maximum length 512) rescored the top 30 first-stage candidates by default (depth is a parameter). It sees the chunk's header text. Model inference is guarded by one process-wide lock (Section 7.3).

### 4.6 Brand/generic aliases

DailyMed labels carry a `genericMedicine` element in the SPL file. Titles of brand-named labels are rewritten as "BRAND (generic)" for BM25, dense embeddings (a full re-embedding, about 50 minutes) and the reranker input. The alias is added to the indexed text, not expanded at query time. The step is idempotent for titles that already carry the generic name.

### 4.7 Cited extractive answers and refusal

The answerer runs the routed hybrid retriever and the cross-encoder, then splits the top 3 chunks into sentences and scores each sentence with the same cross-encoder. It returns the best 2 sentences verbatim with drug title, section title and set ID. If the top chunk's reranker score (a 0 to 1 sigmoid) is below 0.87, it returns no answer. The threshold was chosen to maximize balanced accuracy on v1 (95 answerable) and unanswerable set A (15 questions), checked on v2 (58) and unanswerable set B (15), and set C (15 harder questions) was written afterwards and not used for any choice. No text is generated.

### 4.8 Permission-aware retrieval

Each label has a synthetic access list derived from a hash of its set ID: 50% of labels are public and the rest are restricted to one or two of four groups (cardiology, oncology, neurology, infectious-disease). A principal has a name, groups, an optional tenant and an admin flag. `allowed_mask` returns a boolean mask over chunk positions. Optional tenant ownership (a tenant-owned label is invisible to other tenants and to callers without a tenant) and section rules (a section type can require an extra group on top of the label rule) are opt-in; the document-level behaviour measured in Section 6.8 uses neither. Admins read everything.

Every retriever takes the mask and applies it before ranking: BM25 through a weight mask, dense by masking scores, hybrid and reranker by passing the mask to their first stages. Unreadable text therefore never reaches fusion, the reranker or the answerer. A final check on returned positions filters again, because BM25 with a weight mask can still pad a list with zero-score masked chunks when a query has few matches.

Result cache. The cache key is (index version, query, k, a digest of the mask). Two principals share an entry only if their masks are identical, so permission changes need no invalidation (a changed group list gives a different key). Content changes do need invalidation, which happens when a new index version is installed.

### 4.9 Identity, tokens and revocation

The mask is built from a verified token and not from anything the caller sends in the request body. Required claims are `sub` and `exp`; `groups`, `tenant` and `roles` (with `admin`) are used. Only the configured algorithm is accepted, so unsigned (`alg: none`), wrongly signed, tampered and expired tokens are rejected. RS256 verification uses public keys only (a single key, or several selected by `kid` to allow rotation); the service refuses a PEM key combined with an HMAC algorithm, and the reverse, which blocks the case of an HS256 token forged with the public key as the secret. HS256 with a shared secret remains available. Tokens that claim more than 24 hours of life are refused, which bounds what revocation must remember. A revocation store holds revoked `jti` values and per-user cutoffs, saved to a file by atomic replace so a restart does not un-revoke. A user cutoff refuses tokens issued before it, and also tokens without an `iat`. Revocation is performed through an admin-only endpoint. The service exposes search (keyword, dense, hybrid, rerank), cited answer, an admin-only "not found" log recording who asked and why nothing came back, and a health check.

### 4.10 Kill-safe sync and versioned index publishing

Sync. A connector yields pages ordered by update date from a cursor. Each page's documents and the cursor move are written in one Postgres transaction, so a crash leaves either all of a page with the cursor past it, or none of it with the cursor where it was. The cursor's own day is read again after a restart; a record seen twice is skipped by content hash and a changed one is replaced in the same transaction, and a `UNIQUE (source, set_id)` constraint rules out duplicates. openFDA serves the same SPL labels as DailyMed under the same set ID, so a label already stored from DailyMed is skipped. Deletions are handled by asking the source whether each stored document is still served; a source error must raise, and if more than 20% of a batch looks missing nothing is deleted and an error is raised. A runner syncs each source in turn under a Postgres advisory lock so a second runner cannot start.

Index publishing. After a sync pass, a reindex step builds a version (corpus, vectors, signature) in its own directory and switches a `CURRENT` pointer with an atomic rename. Vectors are reused from the previous version when the text to embed is identical. The signature is computed from the database (source, set ID, version, content hash) and not from sync counts, so a crash between sync and reindex is repaired by the next pass. The engine builds a new version in a background thread (everything for one version is built without touching what requests read, then installed with a few assignments and cache version bumps); a version that cannot be built is logged and the old index keeps serving. A request whose mask was built against the old index and used against the new one receives 503 with `Retry-After` and not a wrong answer. Two versions are kept.

## 5 Evaluation protocol

### 5.1 Question sets

All sets are versioned files under `eval/`. Each answerable question has one gold chunk (and so one gold section and one gold label). Chunk-level scoring needs the exact source chunk; section-level scoring accepts any chunk of the same section.

| Set | Size | Construction | Purpose |
|---|---|---|---|
| v1 | 95 | Written by reading a sampled chunk (one per drug, stratified over 10 section types) | Near-text questions |
| v2 | 58 | Written on drugs not in v1, in patient or clinician wording, avoiding the chunk's distinctive phrases | Paraphrase |
| v3 | 50 | Templated: 25 NDC, 9 CAS, 16 tablet imprints; each identifier occurs in exactly one chunk | Identifier lookup |
| v4 | 26 | Templated: indications or contraindications of a brand-titled label asked by generic name | Brand/generic |
| v5 | 119 | Three model agents wrote one question per sampled chunk (150 chunks, seed 11, drugs not in v1 or v2) from the chunk text only; 29 questions that copied a run of 3 words from the chunk were dropped by script and 2 were declined | Natural wording |
| Unanswerable A, B, C | 15 each | A and B mix made-up drug names, off-topic and medical-adjacent questions; C asks about in-corpus drugs for things a label does not say | Refusal |
| Negation | 80 | 40 labels with both Indications and Contraindications sections; one question of each kind per label | Section intent |
| Narrow user | 40 + 40 | Questions about 40 readable labels and 40 unreadable ones | Access control |

The mean share of a question's words that also occur in its chunk is 0.67 for v1, 0.54 for v2 and 0.44 for v5. The 203-question pooled set (v1 + v2 + v3) is used for access-control and load experiments, and v1 to v5 together give 348 questions.

### 5.2 Metrics

Recall@k (the gold chunk is in the top k), reciprocal rank at 10 (MRR@10), and nDCG@10 (with one gold chunk per question this reduces to a discount on its rank). Section-level variants are reported where noted. Latency is the median per query on the stated machine.

### 5.3 Significance

Differences are tested with a paired bootstrap over questions: 10,000 resamples, percentile 95% intervals, fixed random seed, applied to per-question recall@10 and reciprocal rank (`hss.eval.stats`). A difference is called significant when the interval excludes zero. With 95 questions, one question is about 1 point.

### 5.4 Decision rules

Decision rules were written before a run in these cases, and in no others; everything else in the log is exploratory.

- Adaptive fusion: weight 4 for the keyword list and the identifier detector were fixed once before running and not tuned.
- Refusal threshold: chosen on v1 + set A, tested on v2 + set B, with set C written afterwards and not used for any choice.
- Changing the default retriever (Section 6.7): a new default must be no worse than the current routed hybrid on v1, v2, v3 and v4 beyond noise, and better on v5.
- The same rule is to be applied to v6 when it exists (Section 6.12).

### 5.5 Planned real-query evaluation (v6)

No real user queries have been collected. The tooling is built: an opt-in query log (`hss.service --query-log`) that stores query text, endpoint and time, and deliberately not the user, so the file can be shared for labeling. The `pool` step takes that log or a plain file of one question per line, retrieves the top 3 chunks from each of four retrievers (`bm25_hdr`, `dense`, `hybrid_routed_wide`, `rerank_d30`), merges and shuffles them with a fixed seed, and writes a sheet that shows no rank and no retriever name. A person sets `relevant` to the number of the chunk that answers the question or to "none". The `assemble` step writes `eval/golden_v6.jsonl` in the format the evaluator already reads and counts labeled, none and unlabeled questions. The intended size is 30 to 50 questions; where they will come from is not yet settled. Known biases of pooling [Sparck Jones and van Rijsbergen, 1976]: only pooled chunks can be marked relevant, so a chunk that none of the four retrievers returned counts as not relevant, which favors the retrievers already built; the "none" count is reported to make this visible. Chunk-level scores underestimate when several chunks answer, so section-level numbers are to be read as well.

## 6 Results

All results are on the 959-label corpus, with the aliased corpus from Section 6.4 onward unless stated. Each setting is one run. "Dense" is bge-small; "hybrid" is equal-weight RRF; "routed" is hybrid with identifier routing; "rerank" is a cross-encoder over the first-stage top 30 unless a depth is given.

### 6.1 Baselines on v1 and v2 (un-aliased corpus)

Chunk-level recall@10 / MRR@10.

| Retriever | v1 (95) | v2 (58) | pooled (153) | Median latency |
|---|---|---|---|---|
| BM25 + header | 0.86 / 0.552 | 0.76 / 0.416 | 0.82 / 0.501 | <1 ms |
| Dense | 0.93 / 0.551 | 0.93 / 0.613 | 0.93 / 0.575 | 19 ms |
| Hybrid (RRF) | 0.94 / 0.641 | 0.88 / 0.543 | 0.92 / 0.604 | 20 ms |
| Hybrid + rerank | 0.95 / 0.632 | 0.93 / 0.610 | 0.94 / 0.624 | about 2,100 ms |

On v1 alone (full metric set): BM25 raw text 0.29 recall@1, 0.80 recall@10, 0.92 recall@50, MRR 0.452, nDCG 0.535; BM25 + header 0.40 / 0.86 / 0.98 / 0.552 / 0.627; dense 0.35 / 0.93 / 0.99 / 0.551 / 0.642; hybrid 0.48 / 0.94 / 0.98 / 0.641 / 0.713; dense + rerank 0.47 / 0.95 / 0.98 / 0.655 / 0.728 (1,635 ms); hybrid + rerank 0.45 / 0.95 / 0.98 / 0.632 / 0.710 (2,120 ms). Hardware for latency: Apple M1, 8 GB.

Paired comparisons. On v1, hybrid beats BM25 + header on recall@10 (+0.07) and MRR (+0.089), both significant; dense alone is within noise of BM25 + header on v1. The reranker adds nothing measurable over hybrid on v1 (0.94 against 0.95 recall@10, MRR slightly lower). On v2, dense beats BM25 by +0.17 recall@10 and +0.20 MRR (significant), and plain hybrid is worse than dense (0.88 against 0.93). Pooled over 153 questions, dense, hybrid and hybrid + rerank are all significantly better than BM25 (+0.09 to +0.12 recall@10) but indistinguishable from each other (hybrid minus dense -0.01 [-0.06, +0.03]; rerank minus hybrid +0.03 [-0.02, +0.07]). Weighting dense 1.5x, 2x or 3x in fusion changed recall@10 by no more than 0.01 from dense alone.

An earlier version of the log called the reranker "not worth it" on the 95 questions. On the harder v2 set it recovered the dense score (0.88 to 0.93) but was still not measurably better than hybrid. The defensible statement is no demonstrated gain for about 100 times the latency.

Index choice. At 53k vectors exact FAISS flat search takes 1.8 ms and HNSW 0.05 to 0.2 ms at 98.8% to 99.6% recall; pgvector HNSW takes 1.7 to 2.4 ms at 98.2% to 99.2% recall, with index plus table 194 MB against 82 MB of raw vectors; int8 scalar quantization cut memory 4x for 0.7 points of recall but was slower than flat in FAISS on this CPU. Benchmark queries were corpus vectors, which flatters recall slightly.

### 6.2 Identifier lookups (v3)

| Retriever | recall@10 / MRR@10 | NDC (25) | CAS (9) | imprint (16) |
|---|---|---|---|---|
| BM25 + header | 0.94 / 0.860 | 25 | 9 | 13 |
| Dense | 0.06 / 0.045 | 0 | 0 | 3 |
| Hybrid (equal weight) | 0.60 / 0.280 | 14 | 7 | 9 |
| Hybrid, adaptive weight 4 | 0.82 / 0.489 | | | |
| Routed, first detector | 0.88 / 0.766 | | | |
| Routed, wide detector | 0.94 / 0.860 | | | |
| Hybrid + rerank | 0.94 / 0.886 | 24 | 9 | 14 |
| Adaptive + rerank | 0.96 / 0.906 | | | |

Dense retrieval cannot look up an identifier (-0.88 recall@10 against BM25, significant). Equal-weight hybrid is also significantly worse than BM25 alone (-0.34), because the dense list, which carries no signal here, pushes the one correct BM25 hit down. Up-weighting BM25 to 4 when the query contains an identifier gives 0.82 but is still worse than BM25 alone (-0.12 [-0.22, -0.04]). Skipping the dense list for such queries gave 0.88 (-0.06 [-0.14, 0.00] against BM25 recall, but MRR still lower, significantly). For 5 of the 6 remaining misses the first detector never fired (pure-number imprints such as "337", plain-word imprints such as "CEP"); the wider detector fires on all 50 identifier questions and on the other 224 questions (v1, v2, v4 and 45 unanswerable) fires exactly where the old one did, and routed recall@10 rose from 0.88 to 0.94 (MRR 0.766 to 0.860), equal to BM25 alone. The three remaining misses are ones BM25 also misses. The reranker rescues plain hybrid (candidates remain in hybrid's top 30: recall@50 is 0.96) but costs about 2 s per query.

Caveats specific to this set. The questions are templated, and the wide detector was extended after seeing misfires on four v1 questions and one unanswerable question, so the zero-false-positive count is on questions the detector was tuned against and is not a clean held-out check. The imprint phrase rule fits how the templates are worded. Three BM25 misses came from messy text captured by the question generator (a newline inside the token, a double space, and the imprint "WARFARIN" which tokenizes like the drug name).

### 6.3 Failure analysis before tuning

Before adding health-specific tuning, the 8 misses of adaptive hybrid + rerank on the 153 natural-language questions (recall@10 0.95) were read. In 4 the right drug and section are in the top 10 but a sibling chunk outranks the gold one; in 2 the answer is plausibly contained in several chunks; 1 was a vocabulary issue (celecoxib "chemical class" against a chunk titled with the generic drug name and DESCRIPTION); 1 was a long table caption. None was a synonym gap. The conclusion drawn at the time, that health-specific tuning had little room to show a gain on those sets, was later qualified by v4 and v5.

### 6.4 Brand and generic names (v4)

The failure analysis found no terminology misses, but no question used a drug's other name. The 26 v4 questions ask about a brand-titled label by its generic name (for example "What is sunvozertinib used for?" for ZEGFROVY). Before aliases, the right label was already in BM25's top 10 for 26 of 26 questions, but only chunks that name the generic matched (description, pharmacology); the indications chunk says only "ZEGFROVY is indicated for...". Dense found the right label in 18 of 26.

Recall@10, before then after aliases (MRR@10 where logged):

| Retriever | v4 (26) | v1 (95) | v2 (58) | v3 (50) |
|---|---|---|---|---|
| BM25 + header | 0.08 / 0.058 to 0.50 / 0.318 | 0.86 to 0.86 | 0.76 to 0.76 | 0.94 to 0.94 |
| Dense | 0.08 / 0.077 to 0.88 / 0.644 | 0.93 to 0.91 | 0.93 to 0.93 | 0.06 to 0.06 |
| Hybrid | 0.08 / 0.077 to 0.77 / 0.483 | 0.94 to 0.95 | 0.88 to 0.86 | 0.60 to 0.56 |
| Routed hybrid | 0.08 / 0.077 to 0.77 / 0.483 | 0.94 to 0.95 | 0.88 to 0.86 | 0.88 to 0.84 |
| Hybrid + rerank | 0.08 / 0.077 to 0.85 / 0.521 | 0.95 to 0.94 | 0.93 to 0.95 | 0.94 to 0.96 |

Dense embeddings use the added name best. BM25 gains less because every chunk of the label now contains the generic, so the indications chunk competes with its siblings on weak lexical cues. Plain and routed hybrid land below dense alone (0.77 against 0.88). The other three sets move by at most 0.04 recall@10 in either direction (the largest, v3 routed 0.88 to 0.84, is two questions). Dense-favouring fusion weights (1.5x, 2x, 3x) raised v4 recall@10 only from 0.77 to 0.77, 0.77 and 0.81, lowered v1 MRR from 0.646 to 0.609, 0.592 and 0.590, and on identifiers collapsed recall to 0.06 to 0.08 without routing. Caveats: two templated question types, 26 questions, and the generic name used for questions comes from the same labels as the alias source (different fields of the SPL file and description text). Combination products have several names.

### 6.5 Natural-wording questions (v5)

On 119 questions written without copied wording, the default routed hybrid is no longer the best first stage. Chunk-level recall@1 / recall@10 / MRR@10:

| Retriever | recall@1 | recall@10 | MRR@10 |
|---|---|---|---|
| BM25 + header | 0.24 | 0.66 | 0.369 |
| Dense | 0.45 | 0.94 | 0.623 |
| Routed hybrid | 0.36 | 0.87 | 0.517 |
| Hybrid + rerank (depth 30) | 0.36 | 0.91 | 0.537 |
| Hybrid + RxNorm/MeSH expansion | 0.38 | 0.80 | 0.513 |
| Rerank + expansion | 0.35 | 0.92 | 0.534 |

Section-level recall@10: dense 0.97, hybrid 0.92, reranked 0.94. Paired bootstrap, dense minus hybrid: MRR +0.106 [0.036, 0.176], recall@10 +0.067 [0.017, 0.126], section recall@10 +0.050 [0.000, 0.101]. Dense minus reranked: MRR +0.086 [0.014, 0.158], recall@10 not distinguishable ([-0.008, 0.084]). Reranked minus hybrid: all intervals include zero. BM25 alone falls to 0.66 recall@10 from 0.86 on v1.

Caveats recorded with this result. The set was filtered to remove questions that copy a 3-word run, which removes exactly the questions keyword search is good at, so it is built to be unfriendly to BM25 and the true mix of real queries is unknown. One question writer read only the first 450 characters of each chunk, so about a third of the questions may fit neighbouring chunks of the same section equally well; section-level figures are the safer ones. One run, no resampling of the question writers.

### 6.6 Negative result: RxNorm and MeSH query expansion

Expansion looked up every 1 to 3 word phrase of the query (RxNav exact name to ingredient and brand names; MeSH descriptor or term to entry terms by SPARQL), cached answers on disk, and appended up to 6 new terms to the keyword query only. Chunk-level recall@1 / recall@10 / MRR on the aliased corpus, one run, no intervals:

| Set | Baseline hybrid | + drugs | + conditions | + both | Rerank | Rerank + both |
|---|---|---|---|---|---|---|
| v1 | .51 / .95 / .647 | .40 / .93 / .569 | .42 / .92 / .583 | .38 / .91 / .562 | .45 / .95 / .633 | .46 / .93 / .634 |
| v2 | .41 / .86 / .565 | .36 / .88 / .534 | .36 / .91 / .530 | .33 / .88 / .511 | .45 / .95 / .627 | .43 / .93 / .610 |
| v4 | .42 / .77 / .483 | .46 / .73 / .544 | .42 / .77 / .483 | .46 / .77 / .548 | .42 / .85 / .521 | .42 / .85 / .523 |

Expansion lowers recall@1 and MRR on v1 and v2 (extra terms outrank the right chunk), raises v2 recall@10 slightly (+0.02 to +0.05) and v4 MRR (+0.06, about one or two questions of 26), and does nothing once the reranker is on. Nothing beats the reranked baseline on any set. MeSH adds noise (for example "heart attack" also returns "Cardiovascular Stroke") and RxNorm duplicates most of what the alias file already supplies. Each uncached phrase costs a live call to two public APIs (1.3 to 3.6 s per query cold) and adds a dependency that can fail. Decision: not wired into the service. Not tried: expanding only when the keyword list is weak, down-weighting added terms, expanding the document side, restricting MeSH to terms that occur in the corpus, and a larger plain-language set. The v5 run repeats the finding (expansion does not help).

### 6.7 Negative result: dense-first routing as a new default

After v5, we asked whether the default could change without losing the keyword-friendly sets. The pre-stated rule (Section 5.4) was that a new default must be no worse than routed hybrid on v1, v2, v3 and v4 beyond noise, and better on v5. Variants: dense alone for non-identifier queries ("dense-first"), and dense weight 2, 3 or 5 in fusion for such queries, each also with the reranker at depth 30. Identifier-like queries still go to BM25 only, so v3 is identical for every variant. MRR@10 change against routed hybrid (paired bootstrap 95% interval):

| Variant | v1 (95) | v2 (58) | v4 (26) | v5 (119) |
|---|---|---|---|---|
| Dense-first | -0.086 [-0.149, -0.022] | +0.052 [-0.032, +0.137] | +0.161 [+0.045, +0.288] | +0.101 [+0.034, +0.167] |
| Dense weight 5 | -0.057 [-0.117, -0.001] | +0.042 | +0.062 [+0.029, +0.097] | +0.055 [+0.008, +0.100] |

Dense weights 2 and 3 sit between, with the same v1 loss of -0.053. Pooled over all 348 questions, the MRR change against routed hybrid is +0.032 [-0.002, +0.067] for dense-first, +0.015 [-0.009, +0.039] for weight 5, +0.011 for weight 3 and -0.004 for weight 2 (pooled MRR: hybrid 0.607, dense-first 0.639, weight 5 0.622). With the reranker at depth 30, the change against reranked hybrid is +0.006 [-0.002, +0.014] for dense-first and +0.007 [0.000, +0.014] for dense weight 3, with per-set changes between -0.015 and +0.017; reranked hybrid against plain hybrid pooled is +0.020 [-0.018, +0.057].

Every first-stage alternative fails the rule, because all lose on v1 by an interval that excludes zero while winning on v2, v4 and v5. Which side of that trade is right depends on the real query mix, which this project does not have. With the reranker on, the choice barely matters. The default is unchanged. A deployment without the reranker (CPU only, strict latency) could gain about +0.05 MRR on v2, v4 and v5 with dense weight 5 at a cost of about 0.06 on v1; that is supported as a per-deployment option and not a new default. We did not investigate a reranked variant that took 14 s per query on v1 in one configuration and 2 s in another (probably contention), and no latency is quoted from that run.

### 6.8 Access control

Setup: four users over the 203-question pool (v1, v2, v3): anonymous (reads 47% of chunks), one group (65%), two groups (80%), admin (100%). Strategies: unfiltered, post-filter top 10, post-filter top 50 (keep 10), and pre-filter (the mask). Recall counts only questions whose answer the user may read.

Leakage. Unfiltered search would have shown unreadable chunks for 66 to 96 questions per restricted user (anonymous: 291 to 326 leaked chunks per retriever) and returned the unreadable gold chunk for 87 of 104 hidden-answer questions (BM25, anonymous). Pre-filtering leaked 0 chunks and returned 0 hidden gold chunks for every retriever and user; post-filtering leaks nothing by construction.

Recall@10 on readable questions, anonymous user (average results per query in brackets for post-10):

| Retriever | post-10 | post-50 | pre-filter |
|---|---|---|---|
| BM25 + header | 0.87 (6.7) | 0.89 | 0.89 |
| Dense | 0.71 (7.0) | 0.73 | 0.73 |
| Hybrid routed | 0.95 (7.1) | 0.98 | 0.94 |
| Adaptive hybrid + rerank | 0.98 (7.0) | 1.00 | 1.00 |

Over-fetching 50 and filtering recovers nearly all recall at this readable share. Pre-filter and post-50 differ by 0 to 4 points in both directions, within what 99 questions can resolve, so we do not claim pre-filtering wins on recall at this scale. Pre-filtering added no measurable time for BM25 and dense (1 ms and 18 ms in both cases). Reranker timings from this run (2.5 to 8.7 s) are not used: the machine was busy.

Narrow tenant. A user granted 40 labels (5.1% of chunks), with 40 questions about those labels and 40 about labels they cannot read. Recall@10 on the readable questions, and the number of empty lists out of 40 on the unreadable-topic questions:

| Retriever | post-10 | post-50 | post-200 | pre-filter | empty lists: post-50 / post-200 / pre |
|---|---|---|---|---|---|
| BM25 + header | 0.85 | 0.93 | 0.95 | 0.95 | 17 / 6 / 0 |
| Dense | 0.78 | 0.80 | 0.80 | 0.80 | 14 / 5 / 0 |
| Hybrid routed | 0.97 | 0.97 | 1.00 | 1.00 | 17 / 10 / 0 |

Over-fetching needs a depth that grows as the readable share shrinks; top 10 leaves under 5 results, top 50 still returns an empty list for 14 to 17 of 40 unreadable-topic questions, and even top 200 leaves 5 to 10 empty. Pre-filtering returns a full list every time and matches or beats the best post-filter on recall. The reranker was not run for the narrow-tenant comparison. A full list for a question the user cannot answer means irrelevant readable chunks; the refusal threshold is what turns that into "not found", which motivates the next measurement.

Cache isolation, tenants, section rules and token handling are covered by unit tests (the suite had 63 passing tests at the latest log entry), not by measurement: an admin's cached result is not returned to an anonymous user for the same query; a revoked group's old entry is not served; one tenant never sees another's labels; a boxed-warning section is absent for callers outside its group; RS256 and `kid` selection, revocation and rotation behave as described in Section 4.9. Removing the `jti` check or the `iat` rule makes tests fail. These tests use stub models and generated keys.

### 6.9 Cited answers and refusal

Refusal at threshold 0.87:

| Set | Outcome |
|---|---|
| v1 answerable (calibration) | 95 of 95 answered |
| A unanswerable (calibration) | 15 of 15 refused |
| v2 answerable (held out) | 53 of 58 answered (0.91) |
| B unanswerable (held out) | 15 of 15 refused |
| C hard unanswerable (written afterwards) | 12 of 15 refused (0.80) |

On easy unanswerable questions the top score is near 0 (maximum 0.68). The hard set shows the limit of a relevance score: "Does azithromycin cure the common cold?" (0.99), "dose of amlodipine for dogs" (0.98) and "dose of levetiracetam for cats" (0.95) were answered because retrieved chunks are on the same drug and mention nearby things. A relevance score cannot tell "on the right topic" from "answers the question". The threshold sits at the edge of the calibration data (0.87 is the lowest v1 score), and the held-out set lost 9% of answerable questions at it.

Attribution (`hss.eval.faithfulness`): the 153 answerable questions through the demo's cited-answer path (routed hybrid + reranker, threshold 0.87, aliased corpus), keeping up to four sentences per answer. 148 of 153 were answered and 5 refused. All 591 of 591 cited sentences appear verbatim in the chunk they cite. Against the question's gold source, the first sentence came from the gold chunk for 43%, the gold section for 48% and the gold label for 66%; the second sentence for 28%, 36% and 67%; at least one of two sentences came from the gold label for 82% of answers. A single gold chunk undercounts, since many drugs have several labels. One reader read 14 randomly chosen first sentences from a different label: 8 fully answer the question from another label of the same drug, 4 are partial, and 2 are wrong or non-answers (a carbamazepine/nefazodone sentence for an amitriptyline/MAOI question; the table fragment "Strong CYP3A4 inhibitor(s)"). The 66% label figure is therefore a pessimistic proxy and not an error rate, and we have no measured error rate. Two offline rules for improving the second sentence (a score ratio to the first sentence of 0.5 to 0.95; dropping fragments) did not separate gold-section sentences and were rejected.

Threshold sensitivity: 6 of 153 answerable questions are refused at 0.87 on the calibration file (5 in the faithfulness run; the calibration file used un-aliased embeddings), 4 at 0.85 or lower; the 30 clearly unanswerable questions (A, B) are all refused down to 0.80, and the 3 hard in-corpus ones (C) are answered at every threshold. The threshold was not moved: 45 unanswerable examples are too few to retune on.

Under a narrow mask (40 readable labels, 5.2% of chunks; 40 questions about readable labels, 40 about unreadable labels; threshold 0.87):

| | Narrow user: answered | First sentence from gold label | Cited an unreadable label | Admin: answered |
|---|---|---|---|---|
| Question about a readable label (40) | 38 | 38 of 38 | 0 | 38 |
| Question about an unreadable label (40) | 11 (29 refused) | n/a | 0 | 40 |

An admin's first sentence is from the intended label for 24 of 38 answers, because other labels of the same drug compete. The 11 answered unreadable-topic questions all cite readable labels with top relevance 0.89 to 1.0, so the threshold cannot separate them; they are typically drugs that also have a readable label (for example Xeljanz and tofacitinib, nitroglycerin, indomethacin), so they may be fair answers from another label, but the answers were not each read. One random 5% grant, 80 questions, one run.

### 6.10 Negation and section intent

Eighty questions on 40 labels with both Indications and Contraindications sections ("what is X used to treat" and "in which patients must X not be used"). A hit is a result from the right section of that label in the top 10; "confused" means the best-ranked result from that label came from the opposite section.

| Retriever | Indications hit@10 | Contraindications hit@10 | Confused (contra questions) |
|---|---|---|---|
| BM25 + header | 16/40 | 14/40 | 0 |
| Dense | 38/40 | 32/40 | 6 |
| Hybrid routed (wide) | 26/40 | 22/40 | 4 |
| Hybrid + rerank | 34/40 | 23/40 | 2 |

BM25 finds the right label for 39 of 40 questions but rarely the right section, because questions say "used to treat" where the label says "indicated". Dense is best at section intent but still ranks Indications first for 6 of 40 "must not be used" questions, an error that matters in this domain. Hybrid is worse than dense alone, and the reranker recovers little on contraindications. The questions are templated, 40 labels, one run; no fix was attempted (candidates: section-intent routing, a section-aware reranker input). A medically fine-tuned model of the same size (MedEmbed-small-v0.1, Apache-2.0, 384 dimensions; same chunks and header text, embedded in 25 minutes) did no better: recall@10 dense 0.91 against 0.93 on v1 and v2 (hybrid 0.92 against 0.94 on v1, 0.88 against 0.88 on v2), identifiers 0.06 for both, MRR slightly higher on v1 (0.569 against 0.551) and v2 (0.621 against 0.613), all within about 0.02 and below what 95 and 58 questions can resolve; on the negation check it put the Indications text first for 11 of 40 contraindication questions against 6 of 40 for bge-small. One candidate model was tested, so this says nothing about other medical models or about fine-tuning on this corpus.

### 6.11 Sync, deletion and publishing

Kill test (`hss.sync.crashtest`, needs Postgres): a fake source of 300 records over 30 days in pages of 25. Scenarios: 7 hard exits (`os._exit`) inside page transactions, on page edges and just after; 8 random SIGKILLs from outside, then a clean run; 40 records revised at the source, 5 more SIGKILLs, and a clean run. After each scenario the id set equals the source's, there is one row per id, content hash and chunk count equal a clean load, there are no orphan sections or chunks, and the cursor is at the newest date. The summary document counts these as 13 SIGKILLs in total (8 + 5). The test fails when the cursor commit is moved before the page write on purpose (missing ids and 12 stale documents), and passes again when restored. A live kill against the real ClinicalTrials.gov API left no partial rows and resumed to 799 documents, 799 distinct ids and 4,404 chunks.

Deletion tests: 12 removed records disappear and no others; a source that suddenly returns nothing deletes nothing; a failing source deletes nothing. A live prune over 799 trials and 296 labels deleted 0, as expected. For openFDA, 296 labels loaded with 0 overlapping rows; looking up 12 random DailyMed labels in openFDA found 9 served there and the overlap rule flagged 9 of 9.

Publishing (real database, real models): the first build from the deployed corpus reused 53,275 vectors and embedded the 4,684 openFDA chunks in about 90 s; an unchanged second pass took 0.7 s. A test label inserted into Postgres was embedded (1 chunk), published in 7.3 s, picked up by the running engine on its next request (cache entries 1 to 0), found by search and cited by the answerer; deleting it and refreshing removed it again (57,959 chunks). After the later change that moves the rebuild to a background thread, unit tests show that requests do not rebuild while the watcher runs and that a broken version keeps the old index; request latency during a rebuild was not measured.

Adding trial documents (4,404 chunks) to the index left drug-label recall@10 unchanged for BM25, dense and routed hybrid on v1 (0.86, 0.93, 0.94) and v2 (0.76, 0.93, 0.88), with recall@50 dips of at most 0.02 and MRR moves of at most 0.012; 60 templated title questions on trials reached recall@10 of 1.00 for all three, which shows reachability only.

### 6.12 Real-query evaluation (v6)

Pending. No real questions have been collected, so no v6 run exists, and this paper reports none. When it exists, this section will give: the number of questions and their source; the number labeled and the number marked "none", with the effect of pooling on the retrievers that contributed to the pool; recall@1, recall@10, MRR and section-level recall@10 for `bm25_hdr`, `dense`, `hybrid_routed_wide`, `rerank_d30` and the dense-first variants; the paired bootstrap against routed hybrid; and the outcome of the pre-stated rule from Section 5.4. Until then, the choice between routed hybrid and dense-first rests on model-written questions (Section 6.7).

## 7 Scale and serving

### 7.1 Ten million synthetic vectors

Setup. 10M vectors of 384 dimensions, each a real chunk embedding plus Gaussian noise (sigma 0.05), re-normalised and regenerated from a seed so that exact search can be streamed over all 10M. The data is more clustered than real text (about 187 noisy copies per real vector), so recall is indicative and memory and build numbers are the reliable ones. Index: IVF with 4096 cells (HNSW over the cells for cheap adds) and product quantization at 48 or 96 bytes per vector; 300 queries; ground truth is exact inner-product search over all 10M. Machine: an 8.6 GB laptop.

Memory. Exact fp32 would need 15.4 GB (fp16 7.7 GB, int8 3.8 GB). The PQ indexes are 0.57 GB (48 bytes) and 1.05 GB (96 bytes) on disk. Peak process memory was 2.6 to 3.0 GB during the build and 3.2 to 4.2 GB over the whole run including the exact pass. Training took 85 to 163 s on 500k vectors and adding 10M took 283 to 333 s.

Recall@10 (strict id overlap with the exact top 10 | "near": returned items scoring within 0.01 of the exact 10th best):

| Code size | nprobe 4 | nprobe 16 | nprobe 64 |
|---|---|---|---|
| 48 bytes | 0.31 \| 0.49 | 0.32 \| 0.49 | 0.32 \| 0.49 |
| 96 bytes | 0.56 \| 0.80 | 0.58 \| 0.82 | 0.58 \| 0.82 |

Probing more cells beyond nprobe 4 does not help, so the loss is from compression and not from missing the right cells.

Refine step. Re-scoring the compressed index's best candidates against a 4-bit scalar-quantised copy of the vectors (1.9 GB for 10M; an 8-bit copy at 3.8 GB would not fit this machine), 48-byte codes, strict / near recall:

| Setting | nprobe 16 | nprobe 64 | Median latency (nprobe 16) |
|---|---|---|---|
| PQ48 alone | 0.32 / 0.49 | 0.32 / 0.49 | 1.3 ms |
| + refine, 40 candidates | 0.58 / 0.87 | 0.58 / 0.87 | 11.3 ms |
| + refine, 100 candidates | 0.68 / 0.93 | 0.68 / 0.93 | 14.8 ms |

Total memory with refine is about 2.4 GB against 15.4 GB for exact vectors.

Filtered search at 10M. Using the compressed index with an id bitmap selector (300 queries, reference = exact search restricted to the same ids, nprobe 16, no refine): a mask allowing 50% of ids gave strict / near recall 0.36 / 0.53 and allowing 5% gave 0.48 / 0.61 (unfiltered 0.32 / 0.49); median latency 6.7 ms (50%) and 4.2 ms (5%). Every returned id passed the mask on a small index; at 10M the selector is relied on and not fully checked. Recall is not better because the loss is the compression and not the filter. Refine was not combined with filtering, and the nprobe-64 filtered rows were not collected.

Throughput. After a first run whose one-worker numbers could not be explained, a clean run (`hss.scale_workers`) gave 2,859 queries per second for one worker at nprobe 4 and 1,155 at nprobe 16 (0.32 and 0.85 ms per query), and 11,899 and 4,140 for four workers (4.2x and 3.6x); eight workers added little. The first one-worker level is 15 to 20% below later ones, so order matters a little. Earlier single-thread latencies at nprobe 16 (5.7 to 7.2 ms for PQ96) are higher than the clean PQ48 figure and are confounded by code size and contention; they are upper bounds.

Problems recorded: the first pass of the concurrency test was wrong (OpenMP thread count is per calling thread) and was rerun; one early build died silently after building (cause unknown); a first attempt of the refine run was killed for memory (exit 137) and the generator was rewritten to draw noise in slices (output bit-identical, checked on three block sizes). A pgvector run at 10M was not done: 10M x 384 floats is 15 GB of table plus an HNSW graph, against 16 GB of free disk. A real 10M corpus was not available.

### 7.2 Reranker cost

Fewer first-stage candidates in front of the reranker (routed hybrid first stage; 153 questions):

| Candidates | v1 recall@10 / MRR | v2 recall@10 / MRR | Median latency (v1 run, GPU) |
|---|---|---|---|
| none (hybrid only) | 0.94 / 0.641 | 0.88 / 0.543 | 19 ms |
| 10 | 0.94 / 0.646 | 0.88 / 0.585 | 529 ms |
| 20 | 0.95 / 0.635 | 0.90 / 0.588 | 1,094 ms |
| 30 | 0.96 / 0.634 | 0.93 / 0.610 | 1,729 ms |

Latency is about 55 ms per candidate pair. Ten candidates keep most of the MRR gain on the paraphrased set (+0.042 of the +0.067 that 30 candidates give) at about a third of the cost; recall@10 rises (+0.05) only with 30. On v1 the reranker changes nothing measurable at any depth. A v2 latency figure for 30 candidates (13.6 s) came from a contended machine and is not used. Not tried: batching across requests, a smaller reranker, ONNX or int8 inference.

### 7.3 Load test

Setup (`hss.loadtest`): closed loop, N workers each sending the next query as soon as the last returns, in one process on an 8-core Apple laptop with GPU via MPS, on the real 53,275-chunk corpus, with the 203 evaluation questions in fixed shuffled order. 400 requests per level for routed hybrid and 100 per level for hybrid + rerank of the top 30. In-process: no HTTP or network time.

| Workers | Hybrid QPS | Hybrid p50 / p95 / p99 (ms) | Hybrid + rerank QPS | Rerank p50 / p95 (ms) |
|---|---|---|---|---|
| 1 | 63.9 | 19 / 21 / 25 | 0.6 | 1,773 / 2,437 |
| 2 | 73.7 | 34 / 36 / 39 | 0.5 | 3,568 / 5,833 |
| 4 | 72.0 | 67 / 78 / 110 | 0.5 | 7,837 / 11,367 |
| 8 | 71.8 | 135 / 151 / 228 | 0.5 | 16,588 / 18,621 |

Peak memory was 0.8 GB for hybrid and 1.1 GB with the reranker. Throughput does not scale with workers: it stays near 72 QPS (hybrid) and 0.5 QPS (rerank) while latency grows in proportion to waiting requests, a queue in front of one resource (the model calls). The load test found a crash that unit tests and single-user use did not: with two or more threads, the reranker on the Apple GPU aborted the process (a Metal "command encoder is already encoding" assertion). A process-wide lock around model inference fixed it, at some cost to hybrid throughput: an earlier hybrid-only run before the lock reached 61.5 / 68.1 / 84.9 / 89.5 QPS at 1 / 2 / 4 / 8 workers, a single observation on a busy machine, so the gap at 8 workers (about 18%) is not exact.

CPU-only serving (`HSS_DEVICE=cpu`, same machine, no GPU; 160 requests per level for hybrid, 40 for rerank): routed hybrid reached 80.6 QPS at one worker (p95 18 ms) and 87.2 at four (p95 63 ms), higher than on the GPU (63.9 and 71.8) because encoding a short query is cheap on CPU and avoids device transfer. A reranker with 10 candidates cost 1.2 s per query (0.8 QPS) at one worker and the same throughput at four with p95 7.2 s; on the GPU the same depth costs about 0.5 s. A CPU-only server therefore loses little for plain hybrid and about 2.3x for reranking.

Limits: one machine shared with other tasks, one run per configuration, only 100 requests per reranker level (so p99 is nearly the maximum), closed-loop load, no filtered or cited-answer load, no Postgres or HTTP in the path.

### 7.4 A 512 MB engine and the web application

The full engine holds the corpus text as Python strings (about 150 MB), builds the BM25 index at start (peak above 800 MB) and loads PyTorch and a cross-encoder (about 1 GB), which does not fit a 512 MB host. `hss.lite` keeps the same BM25, dense and routed hybrid retrievers and moves the memory elsewhere: chunk text in SQLite on disk (only the few chunks in a result are read), the BM25 index built once and loaded memory-mapped, the embedding matrix kept as float16 on disk and multiplied in blocks of 8,192 rows, and the query encoder run through ONNX Runtime (fastembed) with the same BAAI/bge-small-en-v1.5 weights. The reranker, and with it the cited answers, are left out; the refusal threshold was calibrated on that cross-encoder's scores, so there is no calibrated substitute.

Checks, all on the 53,275-chunk corpus:
- Recomputing 40 document embeddings with the ONNX encoder gave a cosine of 0.9999 or higher against the stored sentence-transformers vectors (minimum 0.9999, mean 1.0), so the stored vectors are reused.
- On 60 of the v5 questions the lite engine returned the same top-10 list as the full engine for 100% of questions in keyword mode, 90% in dense mode and 87% in hybrid mode. Mean overlap at 10 was 1.000, 1.000 and 0.997, and the top result was the same for every question in all three modes. The differences are order swaps among near-tied scores.
- In a container limited to 512 MB (no swap) the service used about 246 MB after 300 requests across the three modes, with no out-of-memory kill. Median request time over HTTP was 76 ms and p95 120 ms on a laptop (one client, sequential). The first dense query after start took about 1.1 s while the encoder loaded.

The web application (`hss.webapp`) serves a React front end and the API from one process. Accounts are email and password (scrypt, salted); a new account has no groups, so it sees public labels only, and groups are granted by an operator or through three preset demo personas, none of which carries the admin role. Tokens are RS256 with a one-hour lifetime and a `jti`; sign-out revokes the token; repeated failed logins for the same client and address are refused for 15 minutes. These properties are covered by tests that fail when the behaviour is removed (a request path that ignores the mask, a logout that does not revoke, a sign-up that grants a role). On the free Render instance (a fraction of a CPU) the lite engine took about 11 s for the first search after a start and about 3 s for later meaning or hybrid searches, while identifier lookups (keyword only) took 0.2 s; these were a handful of requests, one run. The full engine in a Docker Space on a free 2-vCPU CPU host, with the reranker re-reading 10 candidates instead of 30 (`HSS_RERANK_DEPTH`), answered four questions in 3.1 to 5.5 s each (three supported with citations, scores 0.96 to 0.99; one off-topic question refused at 0.03), reranked search took 3.5 to 4.3 s and hybrid search 0.2 to 0.3 s. Re-scoring the 198 saved answerable and unanswerable questions at depth 10 changed 4 of the 198 refusal decisions relative to depth 30, all on answerable questions and none on the 45 unanswerable ones. In a local container limited to 2 CPUs the same reranker took 10 to 11 s at depth 10 and 24 to 29 s at depth 30, so a laptop's container overstated the Space's cost. Not measured: concurrent load on either deployment, behaviour on a host with a slower disk (SQLite reads and memory-mapped pages depend on it), and the effect of depth 10 on answer quality beyond the accept or refuse decision.

## 8 Discussion

No single method wins. The measurements point in different directions depending on how questions are phrased: BM25 is best on identifiers (0.94 against 0.06 for dense) and worst on paraphrase (0.76 on v2, 0.66 on v5); dense is the reverse. Equal-weight fusion is a hedge, not a free win: it is significantly worse than BM25 alone on identifiers and than dense alone on v2, v4 and v5. Routing by query type was the lever that worked for identifiers; routing the other way (dense-first) trades v1 for v2, v4 and v5 and did not meet its acceptance rule. Which default is right depends on the real query mix, which is the main missing input. A reranker narrows nearly all first-stage differences, but costs about 1.7 s per query for 30 candidates on this machine and serves about one query every two seconds.

Terminology gaps hide until a question uses the other name. The early failure analysis found none, and the v4 questions then showed a large gap (0.08 for every retriever) that an index-side alias fixed for dense retrieval (0.88) without query-time cost. Query-side expansion from RxNorm and MeSH did not help; the alias file already carried most of what RxNorm gave for in-corpus labels.

Filter-before-rank matters more as the readable share shrinks. At 47% to 100% readable, over-fetching and post-filtering is competitive; at 5% it returns empty lists for 14 to 17 of 40 questions the user cannot answer at depth 50. The refusal threshold, not the retrieval layer, turns a full list of irrelevant readable chunks into "not found", and it did so for 29 of 40 questions in the narrow-user test. It cannot separate on-topic from answering (3 of 15 hard unanswerable questions answered), so refusal is a relevance control and not a support check.

Extractive answers remove invented text but not wrong selection. The guarantee measured is that every cited sentence is verbatim from the cited chunk (591 of 591). Whether the chosen sentence answers the question was judged by hand only on a sample of 14. Negation is the largest measured weakness: even the best retriever puts Indications text first for 6 of 40 contraindication questions.

Scale. A compressed index with a low-bit refine copy fits 10M vectors in about 2.4 GB and reaches strict recall@10 of 0.58 to 0.68 on clustered synthetic data. These figures say how the memory arithmetic works out and what a refine step recovers; they do not say what quality a real 10M corpus would reach.

## 9 Limitations and threats to validity

- Question sets are small and model-written. v1 to v5 total 348 questions (95, 58, 50, 26, 119); v1 and v2 were written by the author by reading the gold chunk, v3 and v4 are templated, v5 was written by model agents from the chunk text and filtered against copying a 3-word run. The filter removes the questions keyword search is good at. With these sizes one question is 1 to 4 points and intervals are wide.
- There are no real user queries. The query mix of any deployment is unknown, and the choice of default retriever depends on it. v6 is specified but does not exist.
- One gold chunk per question undercounts when several chunks answer or several labels of the same drug exist. Section-level scores are the safer numbers where logged. One v5 writer saw only the first 450 characters of each chunk.
- One corpus, one document type: 959 DailyMed Rx labels (53,275 chunks), plus openFDA and ClinicalTrials.gov records used in smaller experiments. The labels were selected one per distinct title. Results may not transfer to other document types or to the full label set.
- Overlapping and tuned-against questions: sets overlap in what they test, the wide identifier detector was refined after seeing misfires on v1 questions, and the threshold sits at the edge of its calibration data. Only the pre-stated rules in Section 5.4 are confirmatory.
- One run per setting. No resampling of question writers, no repeated timing runs. Bootstrap intervals cover question sampling, not run-to-run variation or model choice.
- The 10M-vector run is synthetic (real embeddings plus noise, more clustered than real text), so recall is indicative; pgvector at 10M was not run; an 8-bit refine copy did not fit.
- Latency and throughput are from one shared laptop, closed-loop, in-process, with no network. One reranked configuration on v1 took 14 s per query for reasons not investigated; no latency from that run is used.
- Access rules, tenants and groups are synthetic and document-level (plus an opt-in section rule tested only by unit tests). The narrow-user cited-answer test used one random 5% grant and 80 questions. There is no identity-provider integration or key set fetching; keys are read at start-up; revocation lives in one process's memory plus a file; tokens are not bound to a client; the demo's viewer selector is not authentication. Unit tests for access control and tokens use stub models and generated keys.
- Faithfulness is attribution accuracy plus a verbatim check. No generative answer mode exists. The hand check covers 14 sentences by one reader.
- The refusal threshold cannot separate on-topic from answering. The 11 answered unreadable-topic questions in the narrow-user test were not individually read.
- One medical embedding model was compared; nothing is said about fine-tuning on this corpus. Expansion variants were run once without intervals.
- CI runs the logic tests with stub models; it does not run the real models, the database, the evaluation harness or the load test, so a green check does not show retrieval quality is unchanged. The workflow ran on its first pull request on GitHub and both jobs (Python 3.11 and 3.13) passed.
- Request latency during an index rebuild was not measured.

## 10 Ethics and safety

This is a research prototype and not medical advice. It must not be used for clinical decisions. The text it returns is label text only; it generates nothing, and it does not combine, interpret or personalize label content for an individual. It can still return a true but irrelevant or incomplete sentence, a sentence from a different label of the same drug, or a sentence that misses a negation. Users must consult the full label and a clinician or pharmacist. The refusal rule is a relevance threshold and does not guarantee that an answer is supported.

Data. Only public-domain sources are ingested (Section 3). openFDA carries a not-for-clinical-use disclaimer. Labels may contain third-party content that should be verified per label. No patient data is used. Question logging is opt-in and does not store the user's identity, so that query files can be shared for labeling; query text itself could contain personal information, and a log should be read, and any query containing personal details removed, before it is shared for labeling. That step has not been run because no real questions exist yet. Access control in this work is synthetic and demonstrates a mechanism; it is not a certified security design.

## 11 Reproducibility

Code and logs are in the repository `health-semantic-search` (MIT license for code; data keep their own licenses, see `LICENSES.md`). Each experiment is recorded in `ENGINEERING_LOG.md` with setup, metric and decision. Per-question results are under `eval/results/` as JSON files (about 189 files at the time of writing), for example `bm25_hdr.json`, `dense.json`, `hybrid.json`, `hybrid_alias_v4.json`, `dense_v5.json`, `hybrid_dw2_rt_v1.json` through `hybrid_dw5_rt_v5.json`, `dense_routed_rt_v1.json` through `_v5.json`, `acl.json`, `acl_narrow.json`, `answerability.json`, `faithfulness.json`, `answer_mask.json` and `adaptive_rerank*.json`. Question sets are `eval/golden.jsonl` (v1), `golden_v2.jsonl` to `golden_v5.jsonl`, `eval/unanswerable.json`, `eval/negation_questions.json`, and `eval/golden_trials.jsonl`.

Commands (from the README and log):

```bash
docker compose up -d && uv sync                 # Postgres 16 + pgvector on localhost:5440; Python environment
uv run pytest                                    # test suite (63 tests at the latest log entry)
uv run python -m hss.eval.stats bm25_hdr dense hybrid hybrid_rerank   # paired bootstrap; --tags=,_v2 pools sets
uv run python -m hss.eval.run --golden eval/golden_v5.jsonl --retrievers <names> --tag _v5   # evaluator
uv run python -m hss.sync.crashtest              # kill-mid-sync test (needs Postgres)
HSS_JWT_PUBLIC_KEY_FILE=idp.pub uv run python -m hss.service   # search API with RS256 tokens
uv run python -m hss.eval.real pool --queries data/queries.jsonl --out eval/real_sheet.json
uv run python -m hss.eval.real assemble --sheet eval/real_sheet.json --out eval/golden_v6.jsonl
```

The `--tag` and retriever-name flags of `hss.eval.run` are as in its module docstring and in `hss.eval.real`; other scripts (`hss.scale`, `hss.scale_refine`, `hss.scale_workers`, `hss.loadtest`, `hss.eval.faithfulness`, `hss.eval.negation`, `hss.eval.answer_mask`) are modules of the same package. The setup and command of each experiment are in its entry in `ENGINEERING_LOG.md`. Result files were committed with the log entry that reports them, so `git log -- eval/results/<file>` gives the commit; a commit hash was not recorded separately for each file.

Versions. Dense model `BAAI/bge-small-en-v1.5`; reranker `BAAI/bge-reranker-base`; medical model `abhinand/MedEmbed-small-v0.1`; Python 3.11 or later (development on 3.13; CI on 3.11 and 3.13); libraries `bm25s`, `faiss-cpu`, `sentence-transformers`, `numpy`, `pystemmer`, and for the API `fastapi`, `pyjwt`, `cryptography`, `uvicorn`. Locked versions in `uv.lock` at the time of writing: bm25s 0.3.15, sentence-transformers 6.2.0, transformers 5.20.2, torch 2.14.1, numpy 2.4.6, pystemmer 3.1.0, faiss-cpu 1.15.1, fastembed 0.9.1, onnxruntime 1.31.1, fastapi 0.162.0, pyjwt 2.15.1, cryptography 50.0.2, uvicorn 0.54.0, psycopg 3.3.6; earlier experiments ran on the versions locked when they were run, which the git history of `uv.lock` records. Hardware: Apple M1, 8 GB for the early benchmark; an 8-core Apple laptop (8.6 GB reported for the scale run) with MPS for the load test; a CPU-only configuration via `HSS_DEVICE=cpu`. Random seeds: v5 chunk sampling seed 11; bootstrap seed 0 with 10,000 resamples; pooling uses a fixed seed. Public demos: a Gradio Hugging Face Space (`deploy/space/`), a lite web app on Render, and a full-engine Docker Space (`deploy/hf-app/`).

Not reproducible from the repository: the 10M-vector data is regenerated from a seed but needs about 15 GB of disk for the exact pass; v5 depends on the outputs of three model agents, which are stored in `eval/golden_v5.jsonl` and not regenerated.

## References

Each entry was checked against Crossref or arXiv for title, authors, year and venue. Years follow the first public version where a journal version appeared later (Johnson et al. 2019, Malkov and Yashunin 2018, Xiao et al.'s arXiv 2023 version). Page numbers were checked for the entries that give them; re-check them against the publisher's page before submission.

- Bodenreider, O. (2004). The Unified Medical Language System (UMLS): integrating biomedical terminology. Nucleic Acids Research, 32(Database issue), D267-D270.
- Cormack, G. V., Clarke, C. L. A., and Buttcher, S. (2009). Reciprocal rank fusion outperforms Condorcet and individual rank learning methods. In Proceedings of SIGIR 2009.
- Efron, B. (1979). Bootstrap methods: another look at the jackknife. The Annals of Statistics, 7(1), 1-26.
- Gollapudi, S., et al. (2023). Filtered-DiskANN: graph algorithms for approximate nearest neighbor search with filters. In Proceedings of The Web Conference (WWW 2023).
- Johnson, J., Douze, M., and Jegou, H. (2019). Billion-scale similarity search with GPUs. IEEE Transactions on Big Data, 7(3), 535-547.
- Karpukhin, V., Oguz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., and Yih, W. (2020). Dense passage retrieval for open-domain question answering. In Proceedings of EMNLP 2020.
- Lewis, P., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. In Advances in Neural Information Processing Systems 33 (NeurIPS 2020).
- Malkov, Y. A., and Yashunin, D. A. (2018). Efficient and robust approximate nearest neighbor search using hierarchical navigable small world graphs. IEEE Transactions on Pattern Analysis and Machine Intelligence, 42(4), 824-836.
- Nogueira, R., and Cho, K. (2019). Passage re-ranking with BERT. arXiv:1901.04085.
- Reimers, N., and Gurevych, I. (2019). Sentence-BERT: sentence embeddings using Siamese BERT-networks. In Proceedings of EMNLP-IJCNLP 2019.
- Robertson, S., and Zaragoza, H. (2009). The probabilistic relevance framework: BM25 and beyond. Foundations and Trends in Information Retrieval, 3(4), 333-389.
- Sparck Jones, K., and van Rijsbergen, C. J. (1976). Information retrieval test collections. Journal of Documentation, 32(1), 59-75.
- Thakur, N., Reimers, N., Ruckle, A., Srivastava, A., and Gurevych, I. (2021). BEIR: a heterogeneous benchmark for zero-shot evaluation of information retrieval models. NeurIPS 2021 Datasets and Benchmarks Track.
- Tsatsaronis, G., et al. (2015). An overview of the BIOASQ large-scale biomedical semantic indexing and question answering competition. BMC Bioinformatics, 16, 138.
- Voorhees, E., et al. (2021). TREC-COVID: constructing a pandemic information retrieval test collection. SIGIR Forum, 54(1).
- Xiao, S., Liu, Z., Zhang, P., Muennighoff, N., Lian, D., and Nie, J.-Y. (2024). C-Pack: packed resources for general Chinese embeddings. In Proceedings of SIGIR 2024 (arXiv:2309.07597, 2023).

## Appendix A Question set construction

v1 (`eval/golden.jsonl`, 95). Chunks were sampled (one per drug, stratified over 10 section types) and questions written by reading the source chunk, so they overlap with its vocabulary and favor BM25.

v2 (`golden_v2.jsonl`, 58). New drugs; questions in patient or clinician wording that avoid the chunk's distinctive phrases, for example "Why shouldn't someone on disulfiram take cough syrup?" for a chunk that says alcohol-containing preparations.

v3 (`golden_v3.jsonl`, 50), built by `hss.eval.exact`. 25 NDC questions ("What product is NDC 61314-647-25?"), 9 CAS and 16 imprint; one question per drug; nothing paraphrased.

v4 (`golden_v4.jsonl`, 26), built by `hss.eval.brand`. "What is X used for?" and "What are the contraindications of X?" with X the generic name, expecting the indications or contraindications chunk of the brand-titled label. The generic is read from the description and must not appear in any label title. The first 40 generated pairs included 14 generic-named labels in capitals and chemical fragments that were removed by reading the list.

v5 (`golden_v5.jsonl`, 119). 150 chunks sampled with seed 11 from drugs not in v1 or v2, spread over the same ten section types. Three model agents each wrote one question per chunk from the chunk text only, never seeing a search result, using plain or clinical wording and not copying any run of 3 words. `hss.eval.natural` re-checks the rule by script: 29 dropped, 2 declined as unaskable.

Unanswerable sets (`eval/unanswerable.json`). A and B: made-up drug names, off-topic questions and medical-adjacent ones (cost, pharmacies, personal diagnosis). C: in-corpus drugs asked for things a label does not say (dose for dogs, cost, "does it cure the common cold").

Negation (`hss.eval.negation`). 40 labels with both sections, one indications-style and one contraindications-style question each.

Access control (`hss.eval.acl`, `hss.eval.acl_narrow`, `hss.eval.answer_mask`). Pools and grants as in Section 6.8 and 6.9.

## Appendix B Per-set tables

Absolute scores for each routing variant on each set, from `eval/results/<variant>_rt_v<set>.json` (aliased corpus, wide identifier detector, one run; chunk level unless stated). Pooled MRR over all 348 questions is 0.607 for routed hybrid, 0.639 for dense-first and 0.622 for dense weight 5 (Section 6.7). On v3 every variant without the reranker is identical because identifier queries never reach the dense search.

v1 (n = 95)

| Variant | R@1 | R@10 | MRR@10 | Section R@10 |
|---|---|---|---|---|
| Routed hybrid (default) | 0.505 | 0.947 | 0.647 | 0.989 |
| Dense-first | 0.368 | 0.905 | 0.561 | 0.937 |
| Dense weight 2 | 0.400 | 0.947 | 0.594 | 0.989 |
| Dense weight 3 | 0.411 | 0.937 | 0.594 | 0.968 |
| Dense weight 5 | 0.411 | 0.916 | 0.589 | 0.947 |
| Routed hybrid + reranker (depth 30) | 0.453 | 0.947 | 0.633 | 0.968 |
| Dense-first + reranker | 0.463 | 0.958 | 0.650 | 0.989 |
| Dense weight 3 + reranker | 0.463 | 0.958 | 0.643 | 0.989 |

v2 (n = 58)

| Variant | R@1 | R@10 | MRR@10 | Section R@10 |
|---|---|---|---|---|
| Routed hybrid (default) | 0.414 | 0.862 | 0.565 | 0.914 |
| Dense-first | 0.466 | 0.931 | 0.616 | 0.966 |
| Dense weight 2 | 0.414 | 0.897 | 0.572 | 0.914 |
| Dense weight 3 | 0.414 | 0.914 | 0.581 | 0.931 |
| Dense weight 5 | 0.448 | 0.931 | 0.607 | 0.966 |
| Routed hybrid + reranker (depth 30) | 0.448 | 0.948 | 0.627 | 0.983 |
| Dense-first + reranker | 0.431 | 0.931 | 0.612 | 0.966 |
| Dense weight 3 + reranker | 0.466 | 0.931 | 0.638 | 0.983 |

v3 (n = 50)

| Variant | R@1 | R@10 | MRR@10 | Section R@10 |
|---|---|---|---|---|
| Routed hybrid (default) | 0.820 | 0.940 | 0.861 | 0.940 |
| Dense-first | 0.820 | 0.940 | 0.861 | 0.940 |
| Dense weight 2 | 0.820 | 0.940 | 0.861 | 0.940 |
| Dense weight 3 | 0.820 | 0.940 | 0.861 | 0.940 |
| Dense weight 5 | 0.820 | 0.940 | 0.861 | 0.940 |
| Routed hybrid + reranker (depth 30) | 0.860 | 0.920 | 0.883 | 0.920 |
| Dense-first + reranker | 0.860 | 0.920 | 0.883 | 0.920 |
| Dense weight 3 + reranker | 0.860 | 0.920 | 0.883 | 0.920 |

v4 (n = 26)

| Variant | R@1 | R@10 | MRR@10 | Section R@10 |
|---|---|---|---|---|
| Routed hybrid (default) | 0.423 | 0.769 | 0.483 | 0.769 |
| Dense-first | 0.538 | 0.885 | 0.644 | 0.923 |
| Dense weight 2 | 0.423 | 0.769 | 0.511 | 0.769 |
| Dense weight 3 | 0.423 | 0.808 | 0.536 | 0.808 |
| Dense weight 5 | 0.423 | 0.846 | 0.545 | 0.846 |
| Routed hybrid + reranker (depth 30) | 0.423 | 0.846 | 0.521 | 0.846 |
| Dense-first + reranker | 0.423 | 0.846 | 0.521 | 0.846 |
| Dense weight 3 + reranker | 0.423 | 0.846 | 0.521 | 0.846 |

v5 (n = 119)

| Variant | R@1 | R@10 | MRR@10 | Section R@10 |
|---|---|---|---|---|
| Routed hybrid (default) | 0.361 | 0.874 | 0.517 | 0.916 |
| Dense-first | 0.454 | 0.941 | 0.618 | 0.966 |
| Dense weight 2 | 0.378 | 0.874 | 0.537 | 0.916 |
| Dense weight 3 | 0.429 | 0.899 | 0.572 | 0.933 |
| Dense weight 5 | 0.412 | 0.916 | 0.573 | 0.950 |
| Routed hybrid + reranker (depth 30) | 0.361 | 0.908 | 0.537 | 0.941 |
| Dense-first + reranker | 0.361 | 0.933 | 0.548 | 0.958 |
| Dense weight 3 + reranker | 0.361 | 0.916 | 0.543 | 0.958 |

Adaptive-fusion table (un-aliased corpus), recall@10 / MRR@10:

| Retriever | v1 (95) | v2 (58) | v3 identifiers (50) | all 203 |
|---|---|---|---|---|
| BM25 + header | 0.86 / 0.552 | 0.76 / 0.416 | 0.94 / 0.860 | 0.85 / 0.589 |
| Dense | 0.93 / 0.551 | 0.93 / 0.613 | 0.06 / 0.045 | 0.71 / 0.444 |
| Hybrid (equal weights) | 0.94 / 0.641 | 0.88 / 0.543 | 0.60 / 0.280 | 0.84 / 0.524 |
| Hybrid, adaptive | 0.94 / 0.641 | 0.88 / 0.543 | 0.82 / 0.489 | 0.89 / 0.576 |
| Hybrid + rerank | 0.95 / 0.632 | 0.93 / 0.610 | 0.94 / 0.886 | 0.94 / 0.688 |
| Adaptive + rerank | 0.96 / 0.633 | 0.93 / 0.610 | 0.96 / 0.906 | 0.95 / 0.694 |

The pooled column depends on how many identifier questions were written (95, 58, 50), so the per-set columns are the ones to read. Adaptive + rerank is nominally best everywhere, but its edge over plain hybrid + rerank is within noise (v3: +0.02 [0.00, +0.06]).

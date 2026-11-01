# Health semantic search: what I built, what I measured, what I would not claim

A permission-aware search and cited-answer system over public drug labels, built in stages with every experiment logged in
[ENGINEERING_LOG.md](../ENGINEERING_LOG.md). This page is the summary: the design, the numbers that changed my decisions, and
the limits. Every figure below is in the log with its setup. The source documents are public (DailyMed, ClinicalTrials.gov,
openFDA); the access rules, tenants and 10M-vector data are synthetic, and I say where that matters.

## What it does

- Search 959 FDA prescription labels (53,275 chunks) by keyword, by meaning, or both, with an optional cross-encoder reranker.
- Answer a question by quoting label sentences with their source, or say there is no supported answer.
- Enforce who can read what inside the search itself, from a signed token (groups, tenant, admin).
- Keep the corpus current with sync jobs that survive being killed (ClinicalTrials.gov, openFDA).
- Runs without a database in the demo; Postgres + pgvector holds the ingested corpus.

## Design in four decisions

1. **Hybrid retrieval, routed by query type.** BM25 and a small embedding model (bge-small) are fused with reciprocal rank
   fusion. Queries that contain an identifier (NDC, CAS number, tablet imprint) go to keyword search alone.
2. **Access control as a filter before ranking.** Every retriever takes a mask of readable chunks, so unreadable text never
   reaches fusion, the reranker or the answer.
3. **Extractive answers with a refusal rule.** Sentences are copied from retrieved chunks; a relevance threshold turns
   "nothing close enough" into "no supported answer".
4. **Atomic sync.** A page of documents and the cursor commit in one transaction; restarts re-read from the cursor and skip
   unchanged records by content hash.

## What the measurements showed

| Question | Result |
|---|---|
| Does semantic search beat keyword search? | On 153 questions: recall@10 BM25 0.82, dense 0.93, hybrid 0.92, hybrid + rerank 0.94. On paraphrased questions BM25 falls to 0.76 while dense holds at 0.93. |
| Does one method win everywhere? | No. On 50 identifier lookups BM25 scores 0.94 and dense 0.06; plain hybrid 0.60. Routing identifier queries to keyword search brings hybrid to 0.94. |
| What did testing a new kind of question reveal? | Brand-titled labels asked by generic name scored 0.08 for every retriever. Adding the generic name from the label's own metadata to the title lifted dense search to 0.88 (hybrid 0.77) on 26 such questions. |
| Are cited answers honest? | All 591 cited sentences are verbatim in the chunk they cite. 66% of first sentences come from the label the question was written from (a pessimistic proxy; a hand check of 14 misses found 8 full answers from another label of the same drug, 4 partial, 2 wrong). |
| Does it refuse when it should? | 30 of 30 clearly unanswerable questions refused; 12 of 15 harder in-corpus ones refused; 6 of 153 answerable questions refused at the chosen threshold. |
| Does access control leak? | Across 203 questions and four users, unfiltered search would have shown unreadable chunks; masked search leaked none. For a user who can read 5% of chunks, post-filtering the top 50 returned an empty list for 14-17 of 40 questions about unreadable labels; masked search never did. |
| Cited answers for that 5% user | 38 of 40 questions about readable labels answered, all from the right label; 29 of 40 questions about unreadable labels refused; no answer cited an unreadable label. |
| Does sync survive being killed? | A 300-record test with hard exits and 13 SIGKILLs ended with the right set, no duplicates, no orphans. The test fails when the cursor commit is moved ahead of the page write. |
| Does it scale to 10M vectors on a laptop? | On synthetic vectors: 48-byte compression keeps the index at 0.5 GB (exact would be 15.4 GB) but strict recall@10 is 0.32; adding a 4-bit refine copy (1.9 GB) lifts it to 0.58-0.68. |
| How fast is it? | Routed hybrid about 64-74 queries per second on one machine (80-87 on CPU alone), plateauing because of the model call. Reranking 30 candidates costs about 1.7 s per query; 10 candidates about 0.5 s on the GPU, 1.2 s on CPU, and keep most of the ranking gain on paraphrased questions. |
| Does it understand negation? | Not well. For questions like "in which patients must X not be used?", dense search returns the Indications text first for 6 of 40; a medically fine-tuned model of the same size did no better (11 of 40). |

## What I would not claim

- **Real-user quality.** The question sets are small, written by me or templated, and each has one gold chunk, which
  undercounts when several chunks answer. Intervals are wide: one question is 1 to 4 points.
- **Faithfulness in the generative sense.** No text is generated. The guarantee is "every sentence is verbatim", plus a
  measured attribution rate; whether the chosen sentence answers the question is judged only on small samples by hand.
- **Production access control.** Rules are synthetic. Tokens are verified, but there is no identity-provider integration,
  no token revocation before expiry, no per-tenant index, and the demo's "Viewing as" selector is not authentication.
- **10M-vector quality.** The vectors are real embeddings plus noise, more clustered than real text; memory and build
  numbers are reliable, recall figures are indicative. A pgvector run at 10M did not fit this machine's disk.
- **Throughput under real traffic.** One machine shared with other work, closed-loop load, no network, one run per setting.

## Where the effort should go next

1. Replace the synthetic questions with real ones from clinicians or pharmacists, with more than one gold answer each.
2. A section-intent step for negation and "who must not" questions, which is the largest measured weakness.
3. Wire the sync runner to rebuild the index and clear the result cache, then schedule it.
4. Identity-provider keys (RS256), token revocation, and a real login in the demo.
5. A generative answer mode with a measured faithfulness check, if the product needs prose.

## Run it

```bash
docker compose up -d && uv sync                      # Postgres + environment
uv run pytest                                         # 43 tests
HSS_JWT_KEY=<secret> uv run python -m hss.service     # search API (sign tokens with the same secret)
uv run python -m hss.sync.crashtest                   # kill-mid-sync test (needs Postgres)
```

The public demo is a Hugging Face Space (drug-label search, cited answers, a "Viewing as" access selector and a not-found
tab); its code is in `deploy/space/`.

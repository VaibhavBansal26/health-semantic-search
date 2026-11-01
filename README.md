# Health Semantic Search

Permission-aware, cited semantic search over public health documents (drug labels, clinical trials, coverage rules, guidelines, literature). Built stage by stage, with every design decision and measurement logged.

The goal is a product a health system could adopt: self-hostable, answers always cite their source, and "no supported answer" beats a guess.

## Status

All stages are built to a first working version and measured; none is finished to production quality. Ingestion, chunking, an evaluation harness, BM25, dense, hybrid (routed) and reranked retrieval, extractive cited answers with refusal, brand/generic aliases, kill-safe sync (ClinicalTrials.gov, openFDA), permission-aware retrieval with token identity, tenants and a search API, a 10M-vector scale test, load tests, and a demo. Read [docs/WRITEUP.md](docs/WRITEUP.md) for the summary of what was measured and what is not claimed, [ENGINEERING_LOG.md](ENGINEERING_LOG.md) for every experiment, and [ROADMAP.md](ROADMAP.md) for the staged plan. Not done: RxNorm/MeSH expansion, real identity-provider integration, a real 10M corpus, real-user questions.

## Results so far

959 FDA prescription drug labels, 53,275 chunks, 153 golden questions (95 written close to the label text, 58
paraphrased the way a patient or clinician would ask). Right chunk in the top 10:

| Retriever | recall@10 | MRR@10 | median latency |
|---|---|---|---|
| BM25 + drug/section header | 0.82 | 0.501 | <1 ms |
| Dense (bge-small) | 0.93 | 0.575 | 19 ms |
| **Hybrid (RRF)** | 0.92 | 0.604 | 20 ms |
| Hybrid + cross-encoder rerank | 0.94 | 0.624 | ~2,100 ms |

Identifier lookups (NDC, CAS number, tablet imprint; 50 templated questions) reverse the picture: BM25 0.94, dense 0.06,
plain hybrid 0.60, hybrid + rerank 0.94. Weighting BM25 up when the query contains an identifier lifts hybrid to 0.82 (still below BM25 alone); see the engineering log. No single method wins on every question type.

Access control is enforced inside the search: each retriever takes a mask of the chunks a user may read. Across 203 questions
and four users, unfiltered search would have returned unreadable chunks for roughly 70-95 questions per restricted user; the masked search returned none, with no
measurable latency cost. Recall was within a few points of over-fetching and filtering afterwards on this corpus (access rules are synthetic).

Dense, hybrid and reranked search all beat keyword search significantly, and are indistinguishable from each other at
this question count. On the paraphrased questions keyword search falls to 0.76 while dense holds at 0.93. Details,
caveats and confidence intervals are in [ENGINEERING_LOG.md](ENGINEERING_LOG.md).

## Architecture

- Ingestion: DailyMed labels (structure-aware chunking), ClinicalTrials.gov and openFDA connectors with transactional cursors
- Index: BM25 + dense embeddings, with an access mask applied inside every search (document, tenant and section rules)
- Retrieval: route by query type, fuse, optionally rerank, then an extractive cited answer or a refusal
- Evaluation: recall@k, MRR, nDCG on versioned question sets, plus leakage, kill-safety, negation and load tests

## Data

Only sources with a documented, commercial-compatible license are used. Every source is tracked in [LICENSES.md](LICENSES.md).

## Local setup

```bash
docker compose up -d     # Postgres 16 + pgvector on localhost:5440
uv sync                  # Python environment
uv run pytest            # 43 tests
HSS_JWT_PUBLIC_KEY_FILE=idp.pub uv run python -m hss.service   # search API on localhost:8000; RS256 tokens (or HSS_JWT_KEY=<secret> for HS256)
```

Connection string: `postgresql://hss:hss_dev_only@localhost:5440/hss` (override with `DATABASE_URL`).

## License

MIT for the code in this repository. Data sources keep their own licenses (see LICENSES.md).

# Roadmap

Each stage ends with something that runs and a number worth quoting. Stop after any stage and there is still a working demo. Record results in ENGINEERING_LOG.md.

| Stage | Goal | Checkpoint |
|---|---|---|
| 0 | Repo, Postgres + pgvector, license ledger, engineering log | `docker compose up` works |
| 1 | DailyMed ingestion, section-aware chunking | N documents / M chunks in Postgres |
| 2 | BM25 baseline and `/search` endpoint | 20 queries, failure cases noted |
| 3 | Evaluation harness (recall@k, MRR, nDCG), golden set | Baseline table, one-command rerun |
| 4 | Embeddings + pgvector HNSW, FAISS benchmark | BM25 vs general vs medical embeddings |
| 5 | Hybrid fusion (RRF) + cross-encoder reranker | Ablation table |
| 6 | Cited answers, "no supported answer", faithfulness | Faithfulness score, refusal tests |
| 7 | Health tuning: RxNorm/MeSH expansion, negation, fine-tuning | Before/after recall |
| 8 | Live connectors (openFDA, ClinicalTrials.gov), reliable sync | Kill mid-sync, no gaps or duplicates |
| 9 | Tenants and document-level permissions | Isolation test suite, including cache |
| 10 | Production hardening, load test, 10M-vector run | QPS at p95 on N vectors, RAM used |
| 11 | UI, admin "not found" view, write-up, demo | Public link |

## Interview angle per stage

0 Data licensing · 1 Chunking trade-offs · 2 BM25 · 3 nDCG and evaluation design · 4 HNSW, recall/latency · 5 Hybrid search, reranking cost · 6 Hallucination and faithfulness · 7 Fine-tuning vs reranking · 8 Reliable incremental sync · 9 Permissions in vector search · 10 Scaling to 100M vectors · 11 Product thinking

## Working rules

- Evaluation comes before any model change.
- After each stage, log the decision made, the alternative rejected, and the number that proves it.
- No data source enters the repo without a LICENSES.md entry.

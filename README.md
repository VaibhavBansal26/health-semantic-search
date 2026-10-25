# Health Semantic Search

Permission-aware, cited semantic search over public health documents (drug labels, clinical trials, coverage rules, guidelines, literature). Built stage by stage, with every design decision and measurement logged.

The goal is a product a health system could adopt: self-hostable, answers always cite their source, and "no supported answer" beats a guess.

## Status

Stages 1-5 done: ingestion, chunking, evaluation harness, BM25, dense, hybrid and reranked retrieval, index benchmark. See [ROADMAP.md](ROADMAP.md) for the staged plan and [ENGINEERING_LOG.md](ENGINEERING_LOG.md) for experiments and results.

## Results so far

959 FDA prescription drug labels, 53,275 chunks, 95 golden questions (chunk-level, top 10):

| Retriever | recall@10 | MRR@10 | median latency |
|---|---|---|---|
| BM25 + drug/section header | 0.86 | 0.552 | <1 ms |
| Dense (bge-small) | 0.93 | 0.551 | 19 ms |
| **Hybrid (RRF)** | **0.94** | **0.641** | 20 ms |
| Hybrid + cross-encoder rerank | 0.95 | 0.632 | 2,120 ms |

Hybrid beats keyword search significantly; the reranker adds nothing measurable here. Details, caveats and
confidence intervals are in [ENGINEERING_LOG.md](ENGINEERING_LOG.md).

## Planned architecture

- Ingestion: connectors with checkpointed cursors, structure-aware chunking
- Index: hybrid dense (HNSW) + BM25, with document-level permission filtering inside the query
- Retrieval: fuse, group by document, rerank, then cited answer generation
- Evaluation: recall@k, MRR, nDCG on a versioned golden set, run on every change

## Data

Only sources with a documented, commercial-compatible license are used. Every source is tracked in [LICENSES.md](LICENSES.md).

## Local setup

```bash
docker compose up -d     # Postgres 16 + pgvector on localhost:5440
uv sync                  # Python environment
```

Connection string: `postgresql://hss:hss_dev_only@localhost:5440/hss` (override with `DATABASE_URL`).

## License

MIT for the code in this repository. Data sources keep their own licenses (see LICENSES.md).

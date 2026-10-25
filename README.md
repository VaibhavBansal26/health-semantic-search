# Health Semantic Search

Permission-aware, cited semantic search over public health documents (drug labels, clinical trials, coverage rules, guidelines, literature). Built stage by stage, with every design decision and measurement logged.

The goal is a product a health system could adopt: self-hostable, answers always cite their source, and "no supported answer" beats a guess.

## Status

Stage 0: repository setup. See [ROADMAP.md](ROADMAP.md) for the staged plan and [ENGINEERING_LOG.md](ENGINEERING_LOG.md) for experiments and results.

## Planned architecture

- Ingestion: connectors with checkpointed cursors, structure-aware chunking
- Index: hybrid dense (HNSW) + BM25, with document-level permission filtering inside the query
- Retrieval: fuse, group by document, rerank, then cited answer generation
- Evaluation: recall@k, MRR, nDCG on a versioned golden set, run on every change

## Data

Only sources with a documented, commercial-compatible license are used. Every source is tracked in [LICENSES.md](LICENSES.md).

## Local setup

```bash
docker compose up -d     # Postgres 16 + pgvector on localhost:5432
```

Python environment and ingestion scripts arrive in Stage 1.

## License

MIT for the code in this repository. Data sources keep their own licenses (see LICENSES.md).

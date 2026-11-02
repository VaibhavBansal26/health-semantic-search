# Health Semantic Search

Permission-aware, cited semantic search over public health documents. Built stage by stage, with every design decision and
measurement logged. The goal is a product a health system could adopt: self-hostable, answers always cite their source, and
"no supported answer" beats a guess.

**Live app:** https://health-semantic-search.onrender.com (free tier: it sleeps when idle, so the first load can take a minute; search only, no cited answers, see below)
**Research write-up:** [docs/PAPER.md](docs/PAPER.md), a workshop-style draft with every number traced to [ENGINEERING_LOG.md](ENGINEERING_LOG.md)
**Full app with cited answers:** https://huggingface.co/spaces/Vaibhavbansal26/health-semantic-search-app (same React app, reranker and cited answers; free CPU, so answers take 3-6 s)
**Original Gradio demo:** the Space `Vaibhavbansal26/health-semantic-search` (reranker, no accounts)

## What it is

A web app over 959 FDA prescription drug labels (53,275 chunks): sign in (or try a demo persona), search by keyword, meaning
or hybrid, see results with highlighted terms and a link to the source label, and, for an admin, read the queries that found
nothing and revoke tokens. The label text is public. The access groups are synthetic (assigned from a hash of each label's
id) so the permission-aware search can be shown; the mechanism is real, the policy is not.

![The full app on the free Hugging Face Space: sign in with a demo account, pick Hybrid + reranker, ask a question, get a cited answer, then an off-topic question is refused](docs/reranker-demo.gif)

*Six steps captured from the live full-engine Space (about 15 seconds of animation): demo sign-in, the reranker mode, a reranked search (2.8 s), a cited answer quoting the ibuprofen label, and a refusal ("What is the capital of France?", top score 0.029). Screenshots of one real run, not a screen recording; timings on the free CPU vary.*

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Front end | React 18, Vite 5, plain CSS | about 52 kB gzipped; served by the API process, no separate host |
| API | FastAPI, uvicorn | search, accounts, admin; security headers, per-request access mask |
| Accounts and tokens | SQLite, scrypt, PyJWT (RS256) | one-hour tokens with a `jti`, sign-out and admin revocation, login rate limit |
| Keyword search | bm25s with a Snowball stemmer | index built once, loaded memory-mapped |
| Dense search | BAAI/bge-small-en-v1.5 | PyTorch in the full engine, ONNX Runtime (fastembed) in the lite one |
| Fusion and routing | weighted reciprocal rank fusion, identifier router | NDC, CAS and imprint queries go to keyword search only |
| Reranker and answers | bge-reranker-base, extractive answers with a refusal threshold | full engine only (about 2 GB) |
| Access control | boolean mask inside every retriever, mask-keyed cache | document, tenant and section rules |
| Corpus, sync | Postgres 16 + pgvector, DailyMed, openFDA, ClinicalTrials.gov connectors | kill-safe incremental sync, versioned index publishing |
| Evaluation | recall@k, MRR, nDCG, paired bootstrap | five model-written question sets, tooling for a real-query set |
| Packaging | Docker (multi-stage), Render Blueprint (`render.yaml`) | image about 700 MB; runs in under 250 MB of RAM |
| CI | GitHub Actions | pytest on Python 3.11 and 3.13 (PR 15) |

Two engines share one API. The **full** engine (`HSS_ENGINE=full`) has the reranker and cited answers and needs about 2 GB.
The **lite** engine (`hss.lite`, the default) fits 512 MB: chunk text in SQLite, memory-mapped BM25 and float16 vectors, an
ONNX query encoder. On 60 questions it returned the same top result as the full engine in every mode and the same top-10 list
for 87-100% of them (keyword 100%, dense 90%, hybrid 87%; the rest are swaps among near-tied scores). It leaves out the
reranker and therefore the cited answers, because the refusal threshold was calibrated on that model.

## Status

All stages are built to a first working version and measured; none is finished to production quality. Not done: a real
identity provider (accounts here are local), real-user questions (the question sets are model-written), trials in the
search index, a real 10M-document corpus, and a multi-day run of the sync schedule. Accounts live on the host's disk; on
Render's free tier that disk is temporary, so sign-ups can disappear when the service restarts. The free tier also has a
fraction of a CPU: on the live service the first search after a sleep took about 11 s and later meaning or hybrid searches
about 3 s (identifier lookups 0.2 s), against 76 ms median on a laptop. A paid instance removes this.

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
plain hybrid 0.60, hybrid + rerank 0.94. Routing identifier-like queries to BM25 alone brings hybrid to 0.94, equal to BM25 (weighting BM25 up inside the fusion only reached 0.82). No single method wins on every question type.

Access control is enforced inside the search: each retriever takes a mask of the chunks a user may read. Across 203 questions
and four users, unfiltered search would have returned unreadable chunks for roughly 70-95 questions per restricted user; the masked search returned none, with no
measurable latency cost. Recall was within a few points of over-fetching and filtering afterwards on this corpus (access rules are synthetic).

Dense, hybrid and reranked search all beat keyword search significantly, and are indistinguishable from each other at
this question count. On the paraphrased questions keyword search falls to 0.76 while dense holds at 0.93. On a later set of 119
questions written in natural wording (v5), dense search beat the routed hybrid; moving to dense-first routing was tested and
rejected because it loses on the keyword-friendly set (MRR -0.086, interval excludes zero). RxNorm/MeSH query expansion
did not help. Details, caveats and confidence intervals are in [ENGINEERING_LOG.md](ENGINEERING_LOG.md).

## Architecture

- Ingestion: DailyMed labels (structure-aware chunking), ClinicalTrials.gov and openFDA connectors with transactional cursors
- Index: BM25 + dense embeddings, with an access mask applied inside every search (document, tenant and section rules)
- Retrieval: route by query type, fuse, optionally rerank, then an extractive cited answer or a refusal
- Evaluation: recall@k, MRR, nDCG on versioned question sets, plus leakage, kill-safety, negation and load tests

## Data

Only sources with a documented, commercial-compatible license are used. Every source is tracked in [LICENSES.md](LICENSES.md).

## Full engine on a free Hugging Face Space

`deploy/hf-app/` holds a Dockerfile and README for a Docker Space that runs the same React app with the full engine (reranker,
cited answers). It clones this repository at build time and downloads the corpus from the demo Space, so the Space repo
needs only those two files. Tested in Docker on a laptop with the container limited to 2 CPUs: starts in about 17 s, uses
about 1.1 GB, returns supported answers with citations and refuses an off-topic question, and runs as a non-root user.

The cost is speed. With 2 CPUs the reranker took 24-29 s per answer or reranked search at 30 candidates, 17-20 s at 20 and
10-11 s at 10 (three questions per depth, one run; the same decisions were reached at every depth, which is far too few to
say quality is unchanged). `HSS_RERANK_DEPTH` sets the number; the code default stays 30 and the Space Dockerfile sets 10.
The refusal threshold was re-checked at depth 10 on the 198 saved questions: 4 of 198 decisions differ from depth 30, all on
answerable questions (3 on v1, 1 on v2), and none of the 45 unanswerable questions changed. Plain hybrid search is unaffected. On the live free CPU Space (depth 10, 4 questions, one run) an answer took 3.1-5.5 s, a reranked search 3.5-4.3 s and a hybrid
search 0.2-0.3 s: faster than the laptop's Docker VM. The three supported questions were answered with citations and the
off-topic one was refused.

## Run the app

```bash
uv sync --extra lite                                       # no PyTorch
(cd web && npm ci && npm run build)                        # React front end -> web/dist
uv run python -m hss.lite build --corpus deploy/space/data/corpus.jsonl.gz --embeddings deploy/space/data/embeddings_fp16.npy --out data/lite
HSS_ADMIN_EMAIL=you@example.com HSS_ADMIN_PASSWORD='a long passphrase' uv run python -m hss.webapp   # http://127.0.0.1:8000
```

`deploy/space/data` is the demo Space's copy of the corpus and vectors (gitignored; download it from the Space repo).
Front-end development: `cd web && npm run dev` proxies to the API on port 8000. Environment variables are listed at the top of
[src/hss/webapp.py](src/hss/webapp.py).

## Deploy on Render

`render.yaml` describes one free web service built from the `Dockerfile`, which downloads the corpus from the demo Space,
builds the lite artifacts, bakes in the embedding model and builds the front end. Set three secrets in the Render dashboard
(they are never stored in the repository):

| Variable | What |
|---|---|
| `HSS_ADMIN_EMAIL`, `HSS_ADMIN_PASSWORD` | the admin account, created or reset at every start |
| `HSS_JWT_PRIVATE_KEY` | PEM private key for signing tokens (`openssl genrsa 2048`); without it a key is generated at start and every restart signs everyone out |

## Local development

```bash
docker compose up -d     # Postgres 16 + pgvector on localhost:5440
uv sync --extra ml --extra api --extra lite
uv run pytest            # 85+ tests, no database or models needed
HSS_JWT_PUBLIC_KEY_FILE=idp.pub uv run python -m hss.service   # API only, for an external identity provider (RS256; or HSS_JWT_KEY for HS256)
```

Connection string: `postgresql://hss:hss_dev_only@localhost:5440/hss` (override with `DATABASE_URL`).

## Evaluating on real questions

`hss.service --query-log` (or `HSS_QUERY_LOG` for the web app) records query text without the user. `python -m hss.eval.real pool`
builds a labeling sheet from four retrievers' top results with no rank or source shown, and `assemble` turns the labeled sheet
into `eval/golden_v6.jsonl`. No real questions have been scored yet.

## License

MIT for the code in this repository. Data sources keep their own licenses (see LICENSES.md).

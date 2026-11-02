# Hugging Face Docker Space: the full engine (reranker, cited answers) behind the React app. Needs about 2 GB of RAM; the free
# CPU Basic Space has 16 GB.
#   docker build -t hss-full deploy/hf-app          (test locally; the Space builds the same file)
#   docker run -p 7860:7860 --cpus=2 --memory=16g -e HSS_ADMIN_EMAIL=... -e HSS_ADMIN_PASSWORD=... hss-full

ARG REF=main
FROM alpine/git AS fetch
ARG REF
RUN git clone --depth 1 --branch ${REF} https://github.com/VaibhavBansal26/health-semantic-search /repo

FROM node:22-slim AS web
WORKDIR /web
COPY --from=fetch /repo/web/package.json /repo/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY --from=fetch /repo/web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HUB_DISABLE_PROGRESS_BARS=1 HF_HOME=/app/hf-cache
RUN useradd -m -u 1000 user
WORKDIR /app
RUN pip install uv && pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY --from=fetch /repo/pyproject.toml /repo/uv.lock ./
COPY --from=fetch /repo/src ./src
RUN uv pip install --system ".[ml,api]"

# Corpus and vectors come from the demo Space; both models are downloaded now so the Space starts without network access.
RUN python -c "from huggingface_hub import hf_hub_download; [hf_hub_download('Vaibhavbansal26/health-semantic-search', 'data/' + n, repo_type='space', local_dir='/app') for n in ('corpus.jsonl.gz', 'embeddings_fp16.npy')]" \
 && python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('BAAI/bge-reranker-base')"

COPY --from=web /web/dist ./web/dist
RUN mkdir -p /app/state && chown user /app/state   # the model cache is only read at run time, so it can stay root-owned
USER user
ENV HOST=0.0.0.0 PORT=7860 HSS_ENGINE=full HSS_CORPUS=/app/data/corpus.jsonl.gz HSS_EMBEDDINGS=/app/data/embeddings_fp16.npy \
    HSS_STATIC_DIR=/app/web/dist HSS_ACCOUNTS_DB=/app/state/accounts.db HSS_REVOCATIONS=/app/state/revocations.json HF_HUB_OFFLINE=1
EXPOSE 7860
CMD ["python", "-m", "hss.webapp"]

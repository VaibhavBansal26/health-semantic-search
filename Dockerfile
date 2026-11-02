# Web app image: React front end + FastAPI + the lite search engine (no PyTorch, fits 512 MB).
#   docker build -t hss-web .
#   docker run -p 8000:10000 -e HSS_ADMIN_EMAIL=you@example.com -e HSS_ADMIN_PASSWORD='...' hss-web

FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FASTEMBED_CACHE_PATH=/app/models HF_HUB_DISABLE_PROGRESS_BARS=1
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv pip install --system ".[lite]"

# The corpus and its vectors come from the public demo Space, so the repository stays small. The lite artifacts are
# built here (the BM25 index needs about 1 GB while building, far more than the running service does), the raw files
# are deleted, and the embedding model is downloaded now so a cold start does not wait for it.
ARG DATA_REPO=Vaibhavbansal26/health-semantic-search
ENV DATA_REPO=${DATA_REPO}
RUN python -c "import os; from huggingface_hub import hf_hub_download; [hf_hub_download(os.environ['DATA_REPO'], 'data/' + n, repo_type='space', local_dir='/tmp/raw') for n in ('corpus.jsonl.gz', 'embeddings_fp16.npy')]"
RUN python -m hss.lite build --corpus /tmp/raw/data/corpus.jsonl.gz --embeddings /tmp/raw/data/embeddings_fp16.npy --out /app/data/lite \
 && rm -rf /tmp/raw \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY --from=web /web/dist ./web/dist
RUN useradd --create-home app && mkdir -p /app/state && chown -R app /app/state
USER app
ENV HOST=0.0.0.0 PORT=10000 HSS_ENGINE=lite HSS_LITE_DIR=/app/data/lite HSS_STATIC_DIR=/app/web/dist \
    HSS_ACCOUNTS_DB=/app/state/accounts.db HSS_REVOCATIONS=/app/state/revocations.json
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/healthz')"
CMD ["python", "-m", "hss.webapp"]

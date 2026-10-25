---
title: Health Semantic Search
emoji: 💊
colorFrom: blue
colorTo: green
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: mit
short_description: Compare keyword, semantic, hybrid and reranked search over FDA drug labels
---

Search 959 FDA prescription drug labels (DailyMed, public domain) with four retrieval modes: BM25, dense
embeddings (`BAAI/bge-small-en-v1.5`), hybrid fusion (RRF) and a cross-encoder reranker (`BAAI/bge-reranker-base`).

Source code and evaluation results: https://github.com/VaibhavBansal26/health-semantic-search

Demo only. Not medical advice.

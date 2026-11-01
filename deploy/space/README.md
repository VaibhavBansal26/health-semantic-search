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
short_description: Keyword, dense, hybrid and reranked drug label search
---

Search 959 FDA prescription drug labels (DailyMed, public domain) with five modes: four retrieval modes ( BM25, dense
embeddings (`BAAI/bge-small-en-v1.5`), hybrid fusion (RRF) and a cross-encoder reranker (`BAAI/bge-reranker-base`)) and a cited-answer mode that quotes label sentences and refuses when nothing is relevant enough. A "Viewing as" selector applies synthetic access rules (the labels are public; the rules only show the mechanism): restricted labels disappear from results and answers for viewers without access.

Source code and evaluation results: https://github.com/VaibhavBansal26/health-semantic-search

Demo only. Not medical advice.

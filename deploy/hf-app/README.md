---
title: Health Semantic Search App
emoji: 💊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Permission-aware, cited search over FDA drug labels (React app)
---

The full web app: React front end, accounts and login, keyword / meaning / hybrid / reranked search, and extractive cited
answers that refuse when nothing is relevant enough.

The image downloads the source from GitHub at build time (`REF` build argument, default `main`) and the corpus from the
`Vaibhavbansal26/health-semantic-search` Space, so this repository holds only this file and the Dockerfile.

Secrets to set in the Space settings: `HSS_ADMIN_EMAIL`, `HSS_ADMIN_PASSWORD`, `HSS_JWT_PRIVATE_KEY` (PEM). Accounts are
stored on the Space's temporary disk and are reset when the Space restarts. The label text is public; the access groups
are synthetic. Demo only, not medical advice.

Source and evaluation: https://github.com/VaibhavBansal26/health-semantic-search

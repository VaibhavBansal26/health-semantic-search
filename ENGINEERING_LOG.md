# Engineering log

One entry per experiment or decision. Each entry records the setup, the metric, the alternative rejected, and what was learned.

## Template

### YYYY-MM-DD · Stage N · short title
- **Question:**
- **Setup:** (data, model, parameters, commit)
- **Result:** (table or numbers)
- **Decision:**
- **Rejected alternative and why:**
- **Lesson / interview note:**

---

## 2026-10-25 · Stage 0 · repository created
- **Decision:** public repo, MIT code license, data licenses tracked separately in LICENSES.md.
- **Decision:** Postgres + pgvector first; revisit a dedicated vector engine in Stage 10 when filtered ANN at scale is measured.

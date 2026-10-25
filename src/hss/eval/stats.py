"""Bootstrap confidence intervals for the retriever comparison.

With ~95 questions, a 2-point difference is two questions. This resamples the questions
10,000 times to show which differences are real and which are noise.

    uv run python -m hss.eval.stats bm25_hdr dense hybrid hybrid_rerank
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

RESULTS = Path("eval/results")


def per_question(name: str, level: str = "chunk") -> list[int | None]:
    data = json.loads((RESULTS / f"{name}.json").read_text())
    return [p[f"{level}_rank"] for p in data["per_question"]]


def metrics(ranks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-question R@10 (0/1) and reciprocal rank at 10. NaN ranks mean 'not found'."""
    hit = (~np.isnan(ranks)) & (ranks <= 10)
    rr = np.where(hit, 1.0 / np.where(np.isnan(ranks), 1, ranks), 0.0)
    return hit.astype(float), rr


def to_array(ranks: list[int | None]) -> np.ndarray:
    return np.array([np.nan if r is None else r for r in ranks], dtype=float)


def ci(values: np.ndarray, rng: np.random.Generator, reps: int = 10_000) -> tuple[float, float, float]:
    idx = rng.integers(0, len(values), size=(reps, len(values)))
    means = values[idx].mean(axis=1)
    return values.mean(), *np.percentile(means, [2.5, 97.5])


def main() -> None:
    names = sys.argv[1:] or ["bm25_hdr", "dense", "hybrid", "hybrid_rerank"]
    rng = np.random.default_rng(0)
    data = {n: metrics(to_array(per_question(n))) for n in names}

    print(f"{'retriever':16s} {'recall@10 [95% CI]':26s} {'MRR@10 [95% CI]':26s}")
    for n, (hit, rr) in data.items():
        h, rr_ = ci(hit, rng), ci(rr, rng)
        print(f"{n:16s} {h[0]:.2f} [{h[1]:.2f}, {h[2]:.2f}]        {rr_[0]:.3f} [{rr_[1]:.3f}, {rr_[2]:.3f}]")

    base = names[0]
    print(f"\npaired difference vs {base} (same questions, so differences are tighter than the intervals above)")
    for n in names[1:]:
        d_hit = data[n][0] - data[base][0]
        d_rr = data[n][1] - data[base][1]
        h, r = ci(d_hit, rng), ci(d_rr, rng)
        sig = lambda t: "significant" if t[1] > 0 or t[2] < 0 else "within noise"
        print(f"{n:16s} recall@10 {h[0]:+.2f} [{h[1]:+.2f}, {h[2]:+.2f}] {sig(h):13s} | MRR {r[0]:+.3f} [{r[1]:+.3f}, {r[2]:+.3f}] {sig(r)}")


if __name__ == "__main__":
    main()

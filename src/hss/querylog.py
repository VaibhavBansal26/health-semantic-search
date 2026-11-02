"""Opt-in record of the questions people ask, kept so they can later be labeled and scored (see hss.eval.real).

    uv run python -m hss.service --query-log data/queries.jsonl

One JSON line per request: time, endpoint and query text. Who asked is deliberately not stored, so the file can be read
and shared for labeling without tying a health question to a person. Off unless the path is given.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class QueryLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, endpoint: str, query: str) -> None:
        row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "endpoint": endpoint, "query": query}
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

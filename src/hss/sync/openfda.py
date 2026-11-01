"""openFDA drug-label connector. Pulls labels whose effective date is on or after the cursor, oldest first.

    uv run python -m hss.sync.openfda --since 2026-10-20 --max-pages 3

openFDA serves the same SPL labels DailyMed does, as JSON, keyed by the same set id. Overlap rule: a label already stored
from DailyMed is skipped, so the corpus never holds one label twice under two sources. What this source adds is labels
the DailyMed sample does not contain.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Iterator
from datetime import date

import httpx
import psycopg

from hss.sync.core import Record, ensure_schema, prune_missing, run_sync

API = "https://api.fda.gov/drug/label.json"
SOURCE = "openfda"
DEFAULT_DSN = "postgresql://hss:hss_dev_only@localhost:5440/hss"
SKIP_LIMIT = 25000  # openFDA refuses skip values above this

SECTIONS = (
    ("indications_and_usage", "Indications and Usage"),
    ("dosage_and_administration", "Dosage and Administration"),
    ("contraindications", "Contraindications"),
    ("boxed_warning", "Boxed Warning"),
    ("warnings_and_cautions", "Warnings and Precautions"),
    ("warnings", "Warnings"),
    ("adverse_reactions", "Adverse Reactions"),
    ("drug_interactions", "Drug Interactions"),
    ("use_in_specific_populations", "Use in Specific Populations"),
    ("overdosage", "Overdosage"),
    ("description", "Description"),
    ("clinical_pharmacology", "Clinical Pharmacology"),
)


def to_record(label: dict) -> Record | None:
    sections = tuple(
        (title, " ".join(label[field]).strip())
        for field, title in SECTIONS
        if label.get(field) and " ".join(label[field]).strip()
    )
    if not sections:
        return None
    openfda = label.get("openfda", {})
    names = openfda.get("brand_name") or openfda.get("generic_name") or openfda.get("substance_name") or [label["set_id"]]
    t = label["effective_time"]
    return Record(label["set_id"], names[0], sections, date(int(t[:4]), int(t[4:6]), int(t[6:8])), "drug label")


def get_json(client: httpx.Client, params: dict) -> dict:
    for attempt in range(5):
        resp = client.get(API, params=params)
        if resp.status_code == 404:  # openFDA answers 404 when a search matches nothing
            return {"results": [], "meta": {"results": {"total": 0}}}
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2**attempt)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("openFDA kept failing")


def make_fetch(since: str | None, already_have: Callable[[str], bool], page_size: int = 100, max_pages: int | None = None):
    def fetch(cursor: str | None) -> Iterator[list[Record]]:
        start = cursor or since
        if not start:
            raise SystemExit("first run needs --since YYYY-MM-DD")
        params = {"search": f"effective_time:[{start.replace('-', '')} TO 99991231]", "sort": "effective_time:asc", "limit": page_size}
        with httpx.Client(timeout=60, headers={"User-Agent": "health-semantic-search/0.1"}) as client:
            skip, total = 0, None
            for _ in range(max_pages or 10**9):
                data = get_json(client, {**params, "skip": skip})
                total = data["meta"]["results"]["total"]
                rows = data.get("results", [])
                records = [r for label in rows if (r := to_record(label)) and not already_have(r.doc_id)]
                yield records
                skip += page_size
                if not rows or skip >= total:
                    return
                if skip > SKIP_LIMIT:
                    raise RuntimeError(f"more than {SKIP_LIMIT} labels after {start}; start from a later date")
                time.sleep(0.3)

    return fetch


def present_ids(ids: list[str]) -> set[str]:
    """Which of these labels does openFDA still serve? Raises on any HTTP error other than "no match"."""
    with httpx.Client(timeout=60, headers={"User-Agent": "health-semantic-search/0.1"}) as client:
        quoted = " ".join(f'"{i}"' for i in ids)
        data = get_json(client, {"search": f"set_id:({quoted})", "limit": len(ids)})
    return {r["set_id"] for r in data.get("results", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--prune", action="store_true", help="after syncing, delete labels openFDA no longer serves")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()
    with psycopg.connect(args.dsn) as conn, psycopg.connect(args.dsn) as lookup:
        ensure_schema(conn)

        def have(set_id: str) -> bool:  # label already stored from DailyMed
            return lookup.execute("SELECT 1 FROM documents WHERE source = 'dailymed' AND set_id = %s", (set_id,)).fetchone() is not None

        print(run_sync(conn, SOURCE, make_fetch(args.since, have, max_pages=args.max_pages)))
        if args.prune:
            print("pruned", prune_missing(conn, SOURCE, present_ids))


if __name__ == "__main__":
    main()

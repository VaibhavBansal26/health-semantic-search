"""ClinicalTrials.gov connector (API v2). Pulls studies updated on or after the cursor, oldest update first.

    uv run python -m hss.sync.ctgov --since 2026-10-25 --max-pages 5

Studies are stored as documents with source 'clinicaltrials' and three sections (summary, description, eligibility).
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Iterator
from datetime import date

import httpx
import psycopg

from hss.sync.core import Record, ensure_schema, prune_missing, run_sync

API = "https://clinicaltrials.gov/api/v2/studies"
SOURCE = "clinicaltrials"
FIELDS = "NCTId|BriefTitle|OfficialTitle|LastUpdatePostDate|BriefSummary|DetailedDescription|EligibilityCriteria"
DEFAULT_DSN = "postgresql://hss:hss_dev_only@localhost:5440/hss"


def to_record(study: dict) -> Record | None:
    p = study["protocolSection"]
    ident = p["identificationModule"]
    sections = tuple(
        (title, text.strip())
        for title, text in (
            ("Brief Summary", p.get("descriptionModule", {}).get("briefSummary", "")),
            ("Detailed Description", p.get("descriptionModule", {}).get("detailedDescription", "")),
            ("Eligibility Criteria", p.get("eligibilityModule", {}).get("eligibilityCriteria", "")),
        )
        if text and text.strip()
    )
    if not sections:
        return None
    updated = date.fromisoformat(p["statusModule"]["lastUpdatePostDateStruct"]["date"][:10])
    return Record(ident["nctId"], ident.get("briefTitle") or ident.get("officialTitle") or ident["nctId"], sections, updated, "clinical trial")


def get_json(client: httpx.Client, params: dict) -> dict:
    for attempt in range(5):
        try:
            resp = client.get(API, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            if attempt == 4:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def make_fetch(since: str | None, page_size: int = 100, max_pages: int | None = None):
    def fetch(cursor: str | None) -> Iterator[list[Record]]:
        start = cursor or since
        if not start:
            raise SystemExit("first run needs --since YYYY-MM-DD")
        params = {
            "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{start},MAX]",  # inclusive: the cursor day is read again
            "sort": "LastUpdatePostDate:asc",
            "pageSize": page_size,
            "fields": FIELDS,
        }
        with httpx.Client(timeout=60, headers={"User-Agent": "health-semantic-search/0.1"}) as client:
            for n in range(max_pages or 10**9):
                data = get_json(client, params)
                yield [r for s in data.get("studies", []) if (r := to_record(s))]
                if not data.get("nextPageToken"):
                    return
                params = {**params, "pageToken": data["nextPageToken"]}
                time.sleep(0.3)

    return fetch


def present_ids(ids: list[str]) -> set[str]:
    """Which of these studies does ClinicalTrials.gov still serve? Raises on any HTTP error."""
    with httpx.Client(timeout=60, headers={"User-Agent": "health-semantic-search/0.1"}) as client:
        data = get_json(client, {"filter.ids": "|".join(ids), "pageSize": len(ids), "fields": "NCTId"})
    return {s["protocolSection"]["identificationModule"]["nctId"] for s in data.get("studies", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="start date for the first run (YYYY-MM-DD); later runs use the stored cursor")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--prune", action="store_true", help="after syncing, delete studies the API no longer serves")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL", DEFAULT_DSN))
    args = parser.parse_args()
    with psycopg.connect(args.dsn) as conn:
        ensure_schema(conn)
        print(run_sync(conn, SOURCE, make_fetch(args.since, max_pages=args.max_pages)))
        if args.prune:
            print("pruned", prune_missing(conn, SOURCE, present_ids))


if __name__ == "__main__":
    main()

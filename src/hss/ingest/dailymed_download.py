"""Download a sample of DailyMed drug labels (SPL XML) via the DailyMed web services.

Two calls are used:
  GET /spls.json?pagesize=100&page=N   list of labels (set id + title), newest first
  GET /spls/{setid}.xml                the full label as SPL XML

Many labels are repackager copies of the same drug (dozens of "levothyroxine"
entries). To get a varied sample we keep only the first label per distinct
drug title, so 1,000 labels means roughly 1,000 different drugs.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
PAGE_SIZE = 100


def drug_key(title: str) -> str:
    """'LEVOTHYROXINE SODIUM TABLET [BRYANT RANCH PREPACK]' -> 'LEVOTHYROXINE SODIUM TABLET'."""
    return title.split("[")[0].strip().upper()


def list_distinct_labels(client: httpx.Client, limit: int, max_pages: int, doctype: str) -> list[dict]:
    seen: set[str] = set()
    picked: list[dict] = []
    for page in range(1, max_pages + 1):
        resp = client.get(
            f"{BASE}/spls.json", params={"pagesize": PAGE_SIZE, "page": page, "doctype": doctype}
        )
        resp.raise_for_status()
        rows = resp.json()["data"]
        if not rows:
            break
        for row in rows:
            key = drug_key(row["title"])
            if key in seen:
                continue
            seen.add(key)
            picked.append(row)
            if len(picked) >= limit:
                return picked
        time.sleep(0.2)
    return picked


def fetch_label(client: httpx.Client, setid: str, out_dir: Path) -> bool:
    path = out_dir / f"{setid}.xml"
    if path.exists():
        return False  # resume-safe: never download the same label twice
    for attempt in range(3):
        try:
            resp = client.get(f"{BASE}/spls/{setid}.xml")
            resp.raise_for_status()
            path.write_bytes(resp.content)
            return True
        except httpx.HTTPError:
            time.sleep(1.5 * (attempt + 1))
    print(f"failed: {setid}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000, help="number of distinct drugs")
    parser.add_argument("--max-pages", type=int, default=80, help="list pages to scan")
    parser.add_argument(
        "--doctype",
        default="34391-3",
        help="LOINC document type; 34391-3 = human prescription drug label (~52k of 159k labels). "
        "The unfiltered list also holds OTC products, devices and cosmetics.",
    )
    parser.add_argument("--out", type=Path, default=Path("data/raw/dailymed"))
    parser.add_argument("--workers", type=int, default=4, help="keep low, this is a public API")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, headers={"User-Agent": "health-semantic-search/0.1"}) as client:
        labels = list_distinct_labels(client, args.limit, args.max_pages, args.doctype)
        print(f"selected {len(labels)} distinct labels")
        (args.out / "manifest.json").write_text(json.dumps(labels, indent=1))

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(lambda r: fetch_label(client, r["setid"], args.out), labels))
    print(f"downloaded {sum(results)} new files into {args.out}")


if __name__ == "__main__":
    main()

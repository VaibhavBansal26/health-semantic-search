"""Generic (active ingredient) names per label, read from the SPL's own genericMedicine element.

A brand-titled label (ZEGFROVY) never says "sunvozertinib" in its indications chunk, so a question that uses the
generic name cannot match it. These names let the index carry both.
"""

from __future__ import annotations

import json
from pathlib import Path

from dataclasses import replace

from lxml import etree

from hss.corpus import Chunk

NS = {"h": "urn:hl7-org:v3"}
RAW = Path("data/raw/dailymed")
OUT = Path("data/aliases.json")


def generic_names(path: Path) -> list[str]:
    tree = etree.parse(str(path))
    names = {" ".join((n.text or "").split()).lower() for n in tree.iterfind(".//h:genericMedicine/h:name", NS)}
    return sorted(n for n in names if n)


def build() -> dict[str, list[str]]:
    aliases = {p.stem: generic_names(p) for p in sorted(RAW.glob("*.xml"))}
    OUT.write_text(json.dumps(aliases))
    return aliases


if __name__ == "__main__":
    a = build()
    print(len(a), "labels,", sum(bool(v) for v in a.values()), "with a generic name")
    for k in list(a)[:5]:
        print(k[:8], a[k])


def with_aliases(chunks: list[Chunk], aliases: dict[str, list[str]]) -> list[Chunk]:
    """Copies of the chunks whose title also carries the generic name: "ZEGFROVY (sunvozertinib)".

    Every consumer of embed_text (BM25, dense embeddings, reranker) then sees both names.
    """
    out = []
    for c in chunks:
        extra = [n for n in aliases.get(c.key[0], []) if n not in c.doc_title.lower()]
        out.append(replace(c, doc_title=f"{c.doc_title} ({', '.join(extra)})") if extra else c)
    return out

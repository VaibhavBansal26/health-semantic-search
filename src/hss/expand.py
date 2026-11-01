"""Query expansion from RxNorm (drug names) and MeSH (condition names). A pilot: measured before it is used anywhere.

    expand("Can I take Tylenol after a heart attack?")  ->  ["acetaminophen", "Myocardial Infarction", ...]

Every 1-3 word phrase of the query is looked up as an exact name. RxNav (rxnav.nlm.nih.gov) returns the ingredient and
brand names of the same product; the MeSH lookup finds a term, and a SPARQL query returns the other entry terms of its
descriptor ("Heart Attack" -> "Myocardial Infarction"). Answers are cached on disk, including "nothing found", so a
repeated run makes no network calls. A network error is not cached and the phrase contributes nothing that time.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

CACHE = Path("data/expansion_cache.json")
RXNAV = "https://rxnav.nlm.nih.gov/REST"
MESH = "https://id.nlm.nih.gov/mesh"
STOP = frozenset("a an the of in on for to and or with what is are does do can how why when which who whom it its be by as at from this that these those after before during use used take taking".split())
PREFIX_ = """PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
# entry terms of a descriptor, and the descriptor's own name; for a term, the descriptor whose preferred concept holds it
BY_DESCRIPTOR = PREFIX_ + "SELECT DISTINCT ?lab WHERE {{ {{ <{uri}> rdfs:label ?lab }} UNION {{ <{uri}> meshv:preferredConcept/meshv:term/meshv:prefLabel ?lab }} }} LIMIT 8"
BY_TERM = PREFIX_ + """SELECT DISTINCT ?lab WHERE {{ ?c meshv:term <{uri}> . ?d meshv:preferredConcept ?c .
  {{ ?d rdfs:label ?lab }} UNION {{ ?d meshv:preferredConcept/meshv:term/meshv:prefLabel ?lab }} }} LIMIT 8"""


def phrases(query: str, longest: int = 3) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-']+", query)
    out = []
    for n in range(longest, 0, -1):
        for i in range(len(words) - n + 1):
            part = words[i : i + n]
            if part[0].lower() in STOP or part[-1].lower() in STOP or (n == 1 and len(part[0]) < 4):
                continue
            out.append(" ".join(part))
    return out


class Expander:
    def __init__(self, cache: Path = CACHE, pause: float = 0.06):
        self.path, self.pause = cache, pause
        self.cache: dict[str, list[str]] = json.loads(cache.read_text()) if cache.exists() else {}
        self.client = httpx.Client(timeout=30, headers={"User-Agent": "health-semantic-search/0.1"})
        self.errors = 0

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache))

    def _get(self, url: str, **params):
        time.sleep(self.pause)
        resp = self.client.get(url, params=params or None)
        resp.raise_for_status()
        return resp.json()

    def rxnorm(self, phrase: str) -> list[str]:
        key = f"rx:{phrase.lower()}"
        if key not in self.cache:
            try:
                ids = self._get(f"{RXNAV}/rxcui.json", name=phrase).get("idGroup", {}).get("rxnormId", [])
                names: list[str] = []
                if ids:
                    groups = self._get(f"{RXNAV}/rxcui/{ids[0]}/related.json?tty=IN+BN")["relatedGroup"].get("conceptGroup", [])
                    names = [p["name"] for g in groups for p in g.get("conceptProperties", [])]
                self.cache[key] = names
            except httpx.HTTPError:
                self.errors += 1
                return []
        return self.cache[key]

    def mesh(self, phrase: str) -> list[str]:
        key = f"mesh:{phrase.lower()}"
        if key not in self.cache:
            try:
                names: list[str] = []
                for kind, template in (("descriptor", BY_DESCRIPTOR), ("term", BY_TERM)):
                    found = self._get(f"{MESH}/lookup/{kind}", label=phrase, match="exact", limit=1)
                    if found:
                        rows = self._get(f"{MESH}/sparql", query=template.format(uri=found[0]["resource"]), format="JSON", inference="false")
                        names = [b["lab"]["value"] for b in rows["results"]["bindings"]]
                        if names:
                            break
                self.cache[key] = names
            except (httpx.HTTPError, KeyError, ValueError):
                self.errors += 1
                return []
        return self.cache[key]

    def expand(self, query: str, limit: int = 6, use: tuple[str, ...] = ("rx", "mesh")) -> list[str]:
        """Extra terms for the query: drug synonyms first, then condition synonyms, none already in the query."""
        lowered = query.lower()
        extra: list[str] = []
        for source in [{"rx": self.rxnorm, "mesh": self.mesh}[u] for u in use]:
            for phrase in phrases(query):
                for name in source(phrase):
                    if name.lower() not in lowered and name.lower() not in (e.lower() for e in extra):
                        extra.append(name)
        return extra[:limit]


class ExpandedKeyword:
    """Keyword retriever that searches with the query plus its expansion. The dense list and the reranker keep the original."""

    def __init__(self, keyword, expander: Expander, limit: int = 6, use: tuple[str, ...] = ("rx", "mesh")):
        self.keyword, self.expander, self.limit, self.use = keyword, expander, limit, use

    def search(self, query: str, k: int = 50, allowed=None) -> list[int]:
        extra = self.expander.expand(query, self.limit, self.use)
        return self.keyword.search(f"{query} {' '.join(extra)}" if extra else query, k, allowed=allowed)

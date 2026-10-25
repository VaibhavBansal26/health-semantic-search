"""Parse a DailyMed SPL (HL7 v3) label into a document with ordered sections.

An SPL file is a tree: document -> structuredBody -> component -> section, where
sections nest (e.g. "5 Warnings and Precautions" contains "5.1 Hepatotoxicity").
Each section carries a LOINC code that says what kind of section it is, which is
how we recognise "Indications" or "Contraindications" without relying on titles.

We keep every top-level section whole, with its subsections folded in under
their own headings. The chunker then splits that text; the whole section stays
available as the "parent" context for a retrieved chunk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lxml import etree

NS = {"h": "urn:hl7-org:v3"}

# Sections that are machine data or label artwork, not readable prose.
SKIP_CODES = {
    "48780-1",  # SPL product data elements (ingredient/package tables)
    "51945-4",  # package label / principal display panel
    "55106-9",  # SPL listing data elements
}

BLOCK_TAGS = {"paragraph", "item", "caption", "list", "tr", "title", "br"}


@dataclass
class Section:
    ordinal: int
    loinc_code: str | None
    title: str
    text: str


@dataclass
class Label:
    set_id: str
    version: int
    doc_type: str | None
    title: str
    labeler: str | None
    effective_date: date | None
    sections: list[Section] = field(default_factory=list)


def _local(tag: str) -> str:
    return etree.QName(tag).localname


def _clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _render(el: etree._Element, out: list[str]) -> None:
    """Flatten an SPL <text> element to plain text, keeping paragraph and table-row breaks."""
    name = _local(el.tag) if isinstance(el.tag, str) else ""
    if name in {"renderMultiMedia", "observationMedia"}:
        return  # images carry no text
    if name == "table":
        for row in el.iterfind(".//h:tr", NS):
            cells = [_clean(" ".join(c.itertext())) for c in row if isinstance(c.tag, str)]
            cells = [c for c in cells if c]
            if cells:
                out.append(" | ".join(cells) + "\n")
        return
    if el.text:
        out.append(el.text)
    for child in el:
        _render(child, out)
        if child.tail:
            out.append(child.tail)
    if name in BLOCK_TAGS:
        out.append("\n")


def _text_of(section: etree._Element) -> str:
    parts: list[str] = []
    for text_el in section.iterfind("h:text", NS):
        _render(text_el, parts)
    return _clean("".join(parts))


def _title_of(section: etree._Element) -> str:
    title = section.find("h:title", NS)
    if title is not None:
        return _clean(" ".join(title.itertext()))
    code = section.find("h:code", NS)
    if code is None:
        return ""
    name = (code.get("displayName") or "").replace(" SECTION", "").removeprefix("SPL ")
    return name.title()


def _flatten(section: etree._Element, depth: int = 0) -> str:
    """Section text, followed by each subsection under its own heading."""
    chunks = []
    body = _text_of(section)
    if body:
        chunks.append(body)
    for sub in section.iterfind("h:component/h:section", NS):
        heading = _title_of(sub)
        inner = _flatten(sub, depth + 1)
        if inner:
            chunks.append(f"{heading}\n{inner}" if heading else inner)
    return "\n\n".join(chunks)


def _parse_date(value: str | None) -> date | None:
    if value and len(value) >= 8 and value[:8].isdigit():
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return None


def parse_label(path: str | Path) -> Label:
    root = etree.parse(str(path)).getroot()

    # The document <title> is often the "Highlights of prescribing information"
    # boilerplate, so prefer the product name from the structured product data.
    product = root.find(".//h:manufacturedProduct/h:name", NS)
    title_el = root.find("h:title", NS)
    if product is not None and "".join(product.itertext()).strip():
        title = _clean(" ".join(product.itertext()))
    else:
        title = _clean(" ".join(title_el.itertext()))[:200] if title_el is not None else ""
    org = root.find("h:author//h:representedOrganization/h:name", NS)
    eff = root.find("h:effectiveTime", NS)

    doc_code = root.find("h:code", NS)  # e.g. "HUMAN PRESCRIPTION DRUG LABEL"
    label = Label(
        set_id=root.find("h:setId", NS).get("root"),
        version=int(root.find("h:versionNumber", NS).get("value")),
        doc_type=doc_code.get("displayName", "").upper() or None if doc_code is not None else None,
        title=title,
        labeler=_clean(org.text) if org is not None and org.text else None,
        effective_date=_parse_date(eff.get("value") if eff is not None else None),
    )

    top_sections = root.iterfind("h:component/h:structuredBody/h:component/h:section", NS)
    for section in top_sections:
        code_el = section.find("h:code", NS)
        code = code_el.get("code") if code_el is not None else None
        if code in SKIP_CODES:
            continue
        text = _flatten(section)
        if not text:
            continue
        label.sections.append(
            Section(ordinal=len(label.sections), loinc_code=code, title=_title_of(section), text=text)
        )
    return label

from datetime import date
from pathlib import Path

from hss.ingest.chunker import approx_tokens, chunk_section
from hss.ingest.spl_parser import parse_label

FIXTURE = Path(__file__).parent / "fixtures" / "mini_spl.xml"


def test_parser_reads_metadata():
    label = parse_label(FIXTURE)
    assert label.set_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert label.version == 3
    assert label.doc_type == "HUMAN PRESCRIPTION DRUG LABEL"
    assert label.title == "EXAMPLEDRUG"  # product name, not the highlights boilerplate
    assert label.labeler == "Example Pharma Inc."
    assert label.effective_date == date(2026, 1, 5)


def test_parser_skips_machine_sections_and_keeps_order():
    label = parse_label(FIXTURE)
    codes = [s.loinc_code for s in label.sections]
    assert codes == ["34067-9", "34068-7"]  # product data and package label dropped
    assert [s.ordinal for s in label.sections] == [0, 1]


def test_parser_folds_subsections_and_flattens_tables():
    dosage = parse_label(FIXTURE).sections[1].text
    assert "Take once daily." in dosage
    assert "2.1 Renal Impairment" in dosage
    assert "<30 mL/min | 5 mg" in dosage  # table row kept as one line


def test_chunks_respect_size_limit():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 80 for i in range(30))
    chunks = chunk_section(text, max_tokens=200, overlap_tokens=40)
    assert len(chunks) > 1
    assert all(approx_tokens(c) <= 200 for c in chunks)


def test_chunks_overlap_and_lose_nothing():
    paragraphs = [f"Unique statement number {i} about dosing." + " filler" * 20 for i in range(20)]
    # each paragraph is ~45 tokens; overlap is whole paragraphs, so the budget must fit at least one
    chunks = chunk_section("\n\n".join(paragraphs), max_tokens=150, overlap_tokens=60)
    joined = "\n".join(chunks)
    assert all(p in joined for p in paragraphs)
    # the last paragraph of a chunk reappears at the start of the next
    assert chunks[0].split("\n\n")[-1] in chunks[1]


def test_oversize_paragraph_is_split_on_sentences():
    sentence = "The recommended dose is 10 mg once daily with food. "
    chunks = chunk_section(sentence * 100, max_tokens=100, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(approx_tokens(c) <= 100 for c in chunks)
    assert all(c.startswith("The recommended") for c in chunks)


def test_short_section_is_one_chunk():
    assert chunk_section("None.") == ["None."]

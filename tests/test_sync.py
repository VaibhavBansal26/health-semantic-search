from datetime import date

from hss.sync.core import Record
from hss.sync.ctgov import to_record

STUDY = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00000001", "briefTitle": "A trial"},
        "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-10-29"}},
        "descriptionModule": {"briefSummary": " Summary text. "},
        "eligibilityModule": {"eligibilityCriteria": "Adults."},
    }
}


def test_study_becomes_record_with_nonempty_sections_only():
    rec = to_record(STUDY)
    assert rec.doc_id == "NCT00000001" and rec.updated == date(2026, 10, 29)
    assert [t for t, _ in rec.sections] == ["Brief Summary", "Eligibility Criteria"]
    assert rec.sections[0][1] == "Summary text."


def test_study_without_text_is_dropped():
    empty = {"protocolSection": {**STUDY["protocolSection"], "descriptionModule": {}, "eligibilityModule": {}}}
    assert to_record(empty) is None


def test_content_hash_changes_with_content_not_with_date():
    a = Record("X", "T", (("S", "text"),), date(2026, 1, 1))
    assert a.content_hash == Record("X", "T", (("S", "text"),), date(2026, 2, 1)).content_hash
    assert a.content_hash != Record("X", "T", (("S", "text!"),), date(2026, 1, 1)).content_hash


def test_openfda_label_becomes_record_and_skips_empty():
    from hss.sync.openfda import to_record as of_record

    label = {"set_id": "abc", "effective_time": "20261020", "openfda": {"brand_name": ["BRANDX"]},
             "indications_and_usage": ["Used for   pain."], "contraindications": [" "]}
    rec = of_record(label)
    assert rec.doc_id == "abc" and rec.title == "BRANDX" and rec.updated == date(2026, 10, 20)
    assert rec.sections == (("Indications and Usage", "Used for   pain."),)
    assert of_record({"set_id": "x", "effective_time": "20261020"}) is None

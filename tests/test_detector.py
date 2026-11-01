import pytest

from hss.retrievers import is_code_like, is_code_like_wide


@pytest.mark.parametrize("q", [
    "Which tablet is debossed with 337?", "Which tablet is debossed with CEP?", "Which tablet is debossed with WARFARIN?",
    "Which tablet is debossed with A109?", "What is NDC 0093-7146-56?", "What drug has CAS 69-72-7?",
])
def test_identifier_questions_are_routed(q):
    assert is_code_like_wide(q)


@pytest.mark.parametrize("q", [
    "Who should not take valacyclovir?", "What is the dose for 20 mg twice daily?", "How many patients under 18 have suicidal thoughts?",
    "Can it be given to children under two years or 20 pounds?", "Who won the football world cup in 2018?", "What is Ammonia N 13 injection used for?",
    "Is it safe for women over 35 who smoke?", "How many more patients per 1,000 have suicidal thoughts?",
])
def test_ordinary_questions_are_not(q):
    assert not is_code_like_wide(q)


def test_wide_detector_includes_the_old_one():
    assert is_code_like("imprint A109") and is_code_like_wide("imprint A109")

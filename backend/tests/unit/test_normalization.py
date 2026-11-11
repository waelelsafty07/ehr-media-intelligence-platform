from datetime import date

import pytest

from app.domain.canonical import Gender
from app.domain.normalization import (
    Outcome,
    name_key,
    normalize_date,
    normalize_gender,
    normalize_mrn,
    normalize_name,
)

TODAY = date(2025, 1, 1)


@pytest.mark.parametrize("raw,expected,outcome", [
    ("1980-03-14", date(1980, 3, 14), Outcome.OK),
    ("1980/03/14", date(1980, 3, 14), Outcome.RECOVERED),
    ("19800314", date(1980, 3, 14), Outcome.RECOVERED),
    ("03/14/1980", date(1980, 3, 14), Outcome.RECOVERED),   # only MM/DD possible
    ("14/03/1980", date(1980, 3, 14), Outcome.RECOVERED),   # only DD/MM possible
    ("05/05/1980", date(1980, 5, 5), Outcome.RECOVERED),    # same either way
    ("12 Jan 1968", date(1968, 1, 12), Outcome.RECOVERED),
    ("January 12, 1968", date(1968, 1, 12), Outcome.RECOVERED),
    ("2024-02-01T10:30:00Z", date(2024, 2, 1), Outcome.RECOVERED),
])
def test_date_formats_are_normalized(raw, expected, outcome):
    r = normalize_date(raw, today=TODAY)
    assert (r.value, r.outcome) == (expected, outcome)


@pytest.mark.parametrize("raw,reason", [
    ("03/04/2020", "ambiguous_day_month"),   # never guessed
    ("31/02/1990", "not_a_real_date"),
    ("2023-02-30", "not_a_real_date"),
    ("2999-01-01", "in_the_future"),
    ("1850-01-01", "implausible_year"),
    ("yesterday-ish", "unrecognized_format"),
    (["1980-01-01"], "unsupported_type"),
])
def test_invalid_dates_are_flagged_not_guessed(raw, reason):
    r = normalize_date(raw, today=TODAY)
    assert r.value is None and r.outcome == Outcome.INVALID and r.reason == reason


@pytest.mark.parametrize("raw", [None, "", "  ", "N/A", "unknown"])
def test_missing_dates(raw):
    assert normalize_date(raw, today=TODAY).outcome == Outcome.MISSING


@pytest.mark.parametrize("raw,expected", [
    ("M", Gender.MALE), ("male", Gender.MALE), (" Female ", Gender.FEMALE), ("F.", Gender.FEMALE),
    ("non-binary", Gender.OTHER), ("unknown", Gender.UNKNOWN), ("U", Gender.UNKNOWN)])
def test_gender_mapping(raw, expected):
    r = normalize_gender(raw)
    assert r.value == expected and r.outcome == Outcome.OK


def test_gender_missing_and_unrecognized_never_guessed():
    assert normalize_gender(None).outcome == Outcome.MISSING
    r = normalize_gender("banana")
    assert r.value == Gender.UNKNOWN and r.outcome == Outcome.INVALID


@pytest.mark.parametrize("raw,expected", [
    ("MRN-000101", "000101"), ("mrn 000101", "000101"), (" 000101 ", "000101"),
    ("mrn:AB-12 34", "AB1234"), (101010, "101010")])
def test_mrn_normalization_keeps_leading_zeros(raw, expected):
    assert normalize_mrn(raw).value == expected


def test_mrn_outcomes():
    assert normalize_mrn("000101").outcome == Outcome.OK
    assert normalize_mrn("MRN-000101").outcome == Outcome.RECOVERED
    assert normalize_mrn("").outcome == Outcome.MISSING
    assert normalize_mrn("MRN").outcome == Outcome.MISSING
    assert normalize_mrn("12").outcome == Outcome.INVALID          # too short
    assert normalize_mrn("12#34$56").outcome == Outcome.INVALID    # bad characters


def test_name_normalization_and_key():
    assert normalize_name("  Test,   Alex ").value == "Alex Test"
    assert normalize_name(None).outcome == Outcome.MISSING
    assert name_key("Alex Test") == name_key("TEST, alex") == name_key("Álex  Test")
    assert name_key("Alex Test") != name_key("Alex Tester")  # exact, not fuzzy

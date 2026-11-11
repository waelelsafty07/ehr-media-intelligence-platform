import json

import pytest

from app.application.ingestion.csv_strategy import CSVIngestionStrategy
from app.application.ingestion.factory import IngestionStrategyFactory, resolve_format
from app.application.ingestion.json_strategy import JSONIngestionStrategy
from app.application.ingestion.text_strategy import TextIngestionStrategy
from app.domain.canonical import SourceFormat
from app.domain.errors import IngestionParseError, UnsupportedFormatError

J, C, T = JSONIngestionStrategy(), CSVIngestionStrategy(), TextIngestionStrategy()


def test_json_array_single_object_and_wrapper():
    assert len(J.parse(b'[{"mrn":"1"},{"mrn":"2"}]')) == 2
    assert len(J.parse(b'{"mrn":"1"}')) == 1
    assert len(J.parse(b'{"records":[{"mrn":"1"}]}')) == 1


def test_json_nested_structure_is_flattened():
    rec = J.parse(json.dumps({"patient": {"mrn": "000101", "name": "A B"}, "document": {"text": "x"}}).encode())[0]
    assert rec.fields == {"mrn": "000101", "name": "A B", "text": "x"}


def test_json_non_object_item_is_a_record_level_error():
    recs = J.parse(b'[{"mrn":"1"}, 42]')
    assert recs[0].parse_error is None and recs[1].parse_error == "record_not_an_object"


@pytest.mark.parametrize("bad", [b"{not json", b"", b'"just a string"', b"[]", b"\xff\xfe\x00bad"])
def test_json_malformed_inputs_raise_without_echoing_content(bad):
    with pytest.raises(IngestionParseError) as e:
        J.parse(bad)
    assert "not json" not in str(e.value)


def test_csv_parses_rows_with_original_headers():
    recs = C.parse(b'mrn,patient_name,dob\n000201,"Fake, Jamie",1975-11-30\n')
    assert recs[0].fields == {"mrn": "000201", "patient_name": "Fake, Jamie", "dob": "1975-11-30"}


def test_csv_ragged_rows_are_record_level_errors():
    recs = C.parse(b"mrn,name,dob\n000201,A\n000202,B,1980-01-01,extra\n000203,C,1980-01-01\n")
    assert [r.parse_error for r in recs] == ["too_few_columns", "too_many_columns", None]


@pytest.mark.parametrize("bad", [b"", b"foo,bar\n1,2\n", b"mrn,name\n", b"\xff\xfe\xfa"])
def test_csv_invalid_inputs_raise(bad):
    with pytest.raises(IngestionParseError):
        C.parse(bad)


def test_text_single_record_with_labels_and_body():
    rec = T.parse(b"Patient: Casey Test\nMRN: 000301\nDOB: 12 Jan 1968\nReason for visit: cough\nNo fever.\n")[0]
    assert rec.fields["Patient"] == "Casey Test" and rec.fields["MRN"] == "000301"
    assert rec.fields["note"] == "Reason for visit: cough\nNo fever."


def test_text_multiple_records_split_on_separator():
    assert len(T.parse(b"MRN: 1000\nNote body\n---\nMRN: 2000\nOther\n")) == 2


def test_text_empty_raises():
    with pytest.raises(IngestionParseError):
        T.parse(b"  \n ")


def test_all_formats_converge_on_same_canonical_fields():
    from app.domain.canonicalization import resolve_fields
    j = J.parse(b'{"mrn":"000101","name":"Alex Test","dob":"1980-03-14","note":"Cough."}')[0]
    c = C.parse(b"mrn,name,dob,note\n000101,Alex Test,1980-03-14,Cough.\n")[0]
    t = T.parse(b"MRN: 000101\nName: Alex Test\nDOB: 1980-03-14\nCough.\n")[0]
    assert resolve_fields(j.fields) == resolve_fields(c.fields) == resolve_fields(t.fields)


def test_factory_selects_strategy_by_format():
    f = IngestionStrategyFactory()
    assert isinstance(f.for_format(SourceFormat.JSON), JSONIngestionStrategy)
    assert isinstance(f.for_format(SourceFormat.CSV), CSVIngestionStrategy)
    assert isinstance(f.for_format(SourceFormat.TEXT), TextIngestionStrategy)


def test_resolve_format():
    assert resolve_format("a.JSON") == SourceFormat.JSON
    assert resolve_format("a.txt") == SourceFormat.TEXT
    assert resolve_format("data.bin", "csv") == SourceFormat.CSV
    for args in (("a.pdf",), ("a.csv", "xml"), (None,)):
        with pytest.raises(UnsupportedFormatError):
            resolve_format(*args)

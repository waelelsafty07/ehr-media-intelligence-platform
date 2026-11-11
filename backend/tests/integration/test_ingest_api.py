from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples"


def upload(client, name, content, fmt=None):
    data = {"format": fmt} if fmt else None
    return client.post("/ingest", files={"file": (name, content)}, data=data)


def test_ingest_sample_json_end_to_end(client):
    r = upload(client, "records.json", (SAMPLES / "records.json").read_bytes())
    assert r.status_code == 200
    body = r.json()
    statuses = [x["status"] for x in body["results"]]
    assert statuses == ["completed", "duplicate", "completed", "completed"]
    assert body["counts"] == {"completed": 3, "duplicate": 1}
    alex1, dup, sam, alex2 = body["results"]
    assert dup["duplicate_of_record_id"] == alex1["record_id"]
    assert alex2["patient_id"] not in (alex1["patient_id"], None)  # same name, different identity: not merged
    assert any(i["code"] == "missing_optional" and i["field"] == "date_of_birth" for i in sam["issues"])


def test_ingest_sample_csv_and_text(client):
    csv = upload(client, "records.csv", (SAMPLES / "records.csv").read_bytes()).json()
    assert [x["status"] for x in csv["results"]] == ["completed", "rejected"]
    errors = [i for i in csv["results"][1]["issues"] if i["severity"] == "error"]
    assert [(i["code"], i["field"]) for i in errors] == [("invalid_value", "date_of_birth")]
    txt = upload(client, "note.txt", (SAMPLES / "note.txt").read_bytes()).json()
    assert txt["results"][0]["status"] == "completed"


def test_reupload_is_idempotent(client):
    content = (SAMPLES / "records.json").read_bytes()
    first = upload(client, "records.json", content).json()
    second = upload(client, "records.json", content).json()
    assert all(x["already_ingested"] for x in second["results"])
    assert [x["record_id"] for x in first["results"]] == [x["record_id"] for x in second["results"]]


def test_malformed_json_returns_422_envelope(client):
    r = upload(client, "bad.json", b'{"mrn": ')
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ingestion_parse_error"


def test_invalid_csv_returns_422(client):
    assert upload(client, "bad.csv", b"foo,bar\n1,2\n").status_code == 422


def test_unsupported_format_returns_415(client):
    r = upload(client, "x.pdf", b"%PDF")
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_format"


def test_oversized_upload_returns_413(client):
    client.app.state.settings.max_upload_bytes = 10
    r = upload(client, "big.json", b"[" + b" " * 50 + b"]")
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_missing_file_returns_validation_envelope(client):
    r = client.post("/ingest")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_explicit_format_overrides_extension(client):
    r = upload(client, "data.dat", b"mrn,name\n000201,Jamie Fake\n", fmt="csv")
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "completed"


def test_internal_errors_are_not_leaked(client):
    client.app.state.session_factory = None  # break the dependency
    r = upload(client, "a.json", b'[{"mrn":"000101"}]')
    assert r.status_code == 500 and "NoneType" not in r.text

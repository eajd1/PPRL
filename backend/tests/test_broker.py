"""Run integration tests only against a disposable PostgreSQL database named pprl_test*."""
import csv
import hashlib
import io
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from src.broker.api import create_app
from src.broker.config import Settings, credential_hash
from src.broker.models import (Base, GlobalPatient, HospitalRecord, Membership, RecordVersion,
                               ReviewCase, Submission, AuditEvent)
from src.broker.scoring import WeightedScorer
from src.broker.validation import COLUMNS, ValidationError, validate_csv
from src.broker.worker import process_next
from src.hospital.combining import build_combination_strings


def row(site="1", pid="001", seed="person", positions=None):
    positions = set(range(1, 15)) if positions is None else set(positions)
    return {"site_id": site, "local_patient_id": pid, "token_version": "v4", "key_id": "test-key", "method": "hmac_sha256",
            **{f"token{i}": hashlib.sha256(f"{seed}:{i}".encode()).hexdigest() if i in positions else "" for i in range(1, 15)}}


def csv_bytes(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def config():
    return Settings(database_url=os.environ.get("BROKER_TEST_DATABASE_URL", ""), admin_hash=credential_hash("admin-test"),
                    hospitals={s: credential_hash(f"hospital-{s}") for s in ("1", "2", "3")}, key_ids=("test-key",))


def as_version(r):
    return SimpleNamespace(tokens=[r[f"token{i}"] for i in range(1, 15)], **{k: r[k] for k in ("key_id", "method", "token_version")})


def test_hospital_export_is_accepted_by_broker(tmp_path):
    from src.hospital.combining import COMBINATIONS
    from src.hospital import token_generator as tg
    path = tmp_path / "hospital1_combinations.csv"
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["hospital_id", "local_patient_id", *[c for c, _ in COMBINATIONS]])
        writer.writeheader()
        writer.writerow({"hospital_id": "1", "local_patient_id": "001",
                         **{c: "synthetic" for c, _ in COMBINATIONS}})
    report = tg.tokenise_file(path, tmp_path / "tokens", tmp_path / "reports", "hmac_sha256", b"x" * 32, "test-key")
    data = Path(report["token_file"]).read_bytes()
    records, duplicates = validate_csv(data, "1", config())
    assert duplicates == 0 and len(records) == 1
    assert records[0]["local_patient_id"] == "001"
    assert len(records[0]) == 19
    assert all(len(records[0][f"token{i}"]) == 64 for i in range(1, 15))


def test_validation_rejects_old_or_incomplete_exports():
    for remove in [("token_version", "key_id", "method"), ("token14",), ("token13",)]:
        values = {k: v for k, v in row().items() if k not in remove}
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=list(values))
        writer.writeheader()
        writer.writerow(values)
        with pytest.raises(ValidationError):
            validate_csv(out.getvalue().encode(), "1", config())


def test_missing_fields_are_not_hashed():
    assert all(not v for v in build_combination_strings({}).values())
    assert build_combination_strings({"first_name": "a", "last_name": "b", "fake_medicare_id": "1234567890"})["medicare_last_first_initial"]


def test_group_caps_and_thresholds():
    scorer = WeightedScorer()
    assert scorer.score(as_version(row()), as_version(row()))["score"] == 24
    for positions, expected, classification in [([3, 4], 6, "review"), ([1, 5], 13, "match"),
                                                ([3, 5, 14], 12, "match"), ([1, 2, 3, 4], 8, "review"),
                                                ([3, 10], 11, "review"), ([5], 5, "below_threshold"), ([], 0, "below_threshold")]:
        result = scorer.score(as_version(row()), as_version(row(positions=positions)))
        assert (result["score"], result["classification"]) == (expected, classification)
    other = row(); other["key_id"] = "different"
    assert scorer.score(as_version(row()), as_version(other))["score"] == 0


def test_validation_and_duplicate_handling():
    settings = config()
    rows, count = validate_csv(csv_bytes([row(), row()]), "1", settings)
    assert count == 1 and rows[0]["local_patient_id"] == "001"
    for bad in [row(site="2"), row(pid=""), {**row(), "token1": "invalid"}, {**row(), "token14": "invalid"}, {**row(), "token_version": "v2"}]:
        with pytest.raises(ValidationError):
            validate_csv(csv_bytes([bad]), "1", settings)
    with pytest.raises(ValidationError):
        validate_csv(csv_bytes([row(), row(seed="different")]), "1", settings)
    with pytest.raises(ValidationError):
        validate_csv(csv_bytes([row()]).replace(b"token14", b"token13"), "1", settings)
    assert validate_csv(csv_bytes([row(positions=[])]), "1", settings)[0]
    settings.max_rows = 1
    with pytest.raises(ValidationError):
        validate_csv(csv_bytes([row(), row()]), "1", settings)


@pytest.fixture(scope="session")
def database():
    url = os.environ.get("BROKER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set BROKER_TEST_DATABASE_URL to a disposable PostgreSQL pprl_test database")
    from sqlalchemy.engine import make_url
    assert make_url(url).database.startswith("pprl_test"), "Refusing to modify a non-test database"
    engine = create_engine(url, hide_parameters=True)
    from alembic.config import Config
    from alembic import command
    previous = os.environ.get("BROKER_DATABASE_URL")
    os.environ["BROKER_DATABASE_URL"] = url
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    yield engine
    if previous is None:
        os.environ.pop("BROKER_DATABASE_URL", None)
    else:
        os.environ["BROKER_DATABASE_URL"] = previous
    engine.dispose()


@pytest.fixture
def client(database):
    with database.begin() as db:
        db.execute(text("TRUNCATE " + ", ".join(Base.metadata.tables) + " RESTART IDENTITY CASCADE"))
    app = create_app(config(), start_worker=False)
    with TestClient(app) as client:
        yield client


def submit(client, rows, site="1", key=None, expected=202):
    response = client.post("/api/v1/submissions", headers={"Authorization": f"Bearer hospital-{site}",
                           "Idempotency-Key": key or uuid.uuid4().hex}, files={"file": ("tokens.csv", csv_bytes(rows), "text/csv")})
    assert response.status_code == expected, response.text
    return response.json()


def result(client, sid, site="1"):
    response = client.get(f"/api/v1/submissions/{sid}/results", headers={"Authorization": f"Bearer hospital-{site}"})
    assert response.status_code == 200
    return response.json()["items"]


def process(client):
    assert process_next(client.app.state.sessions)


def test_three_hospitals_and_timestamps(client):
    ids = []
    for site, pid in [("1", "001"), ("2", "different-id"), ("3", "another-id")]:
        s = submit(client, [row(site, pid)], site)
        process(client)
        ids.append(result(client, s["id"], site)[0]["global_patient_id"])
    assert len(set(ids)) == 1
    old = submit(client, [row()], key="new-arrival")
    process(client)
    r = result(client, old["id"])[0]
    assert r["latest_received_at"] != r["content_changed_at"]
    replay = submit(client, [row()], key="new-arrival")
    assert replay["id"] == old["id"] and replay["received_at"] == old["received_at"]
    assert not process_next(client.app.state.sessions)
    submit(client, [row(seed="changed")], key="new-arrival", expected=409)


def test_equal_local_ids_not_identity_evidence(client):
    a = submit(client, [row()]); process(client)
    b = submit(client, [row("2", seed="different")], "2"); process(client)
    assert result(client, a["id"])[0]["global_patient_id"] != result(client, b["id"], "2")[0]["global_patient_id"]


def test_private_healthcare_evidence_is_stored_and_links_hospitals(client):
    from src.broker.models import RecordToken, Assessment
    a = submit(client, [row(positions=[2, 10])]); process(client)
    b = submit(client, [row("2", "different", positions=[2, 10])], "2"); process(client)
    assert result(client, a["id"])[0]["global_patient_id"] == result(client, b["id"], "2")[0]["global_patient_id"]
    with client.app.state.sessions() as db:
        assert set(db.scalars(select(RecordToken.position))) == {2, 10}
        assert all(len(v.tokens) == 14 for v in db.scalars(select(RecordVersion)))
        assert db.scalar(select(Assessment.score)) == 13


def test_v3_queue_and_snapshots_remain_readable(client):
    from src.broker.models import Assessment
    def legacy(site):
        return {"site_id":site,"local_patient_id":"old", "token_version":"v3", "key_id":"test-key", "method":"hmac_sha256",
                **{f"token{i}":hashlib.sha256(f"old:{i}".encode()).hexdigest() for i in range(1,26)}}
    ids=[]
    for site in ("1","2"):
        a=submit(client,[row(site)],site)
        with client.app.state.sessions.begin() as db:
            submission=db.scalar(select(Submission).where(Submission.id==uuid.UUID(a["id"])))
            submission.payload=[legacy(site)]
        process(client)
        ids.append(result(client,a["id"],site)[0]["global_patient_id"])
    assert ids[0] == ids[1]
    with client.app.state.sessions() as db:
        assert db.scalar(select(Assessment.scoring_version)) == "grouped-24-v2"
        assert all(len(v.tokens)==25 for v in db.scalars(select(RecordVersion)))
    incoming=submit(client,[row(pid="old")]);process(client)
    update=result(client,incoming["id"])[0]
    assert update["global_patient_id"] == ids[0]
    assert update["status"] == "pending_review"
    with client.app.state.sessions() as db:
        record=db.scalar(select(HospitalRecord).where(HospitalRecord.site_id=="1"))
        assert db.get(RecordVersion,record.matching_version_id).token_version=="v3"
        assert db.get(RecordVersion,record.latest_version_id).token_version=="v4"


def test_review_range_and_blank_records(client):
    submit(client, [row()]); process(client)
    b = submit(client, [row("2", positions=[3])], "2"); process(client)
    assert result(client, b["id"], "2")[0]["global_patient_id"] is None
    c = submit(client, [row("3", positions=[])], "3"); process(client)
    r = result(client, c["id"], "3")[0]
    assert r["global_patient_id"] and r["status"] == "insufficient_evidence"


def test_transitive_chain_does_not_merge(client):
    submit(client, [row(positions=[1,5])]); process(client)
    submit(client, [row("2")], "2"); process(client)
    c = submit(client, [row("3", positions=[10,11,12,13,14])], "3"); process(client)
    assert result(client, c["id"], "3")[0]["status"] == "pending_review"
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(GlobalPatient)) == 1


def test_ambiguity_and_same_hospital_second_id(client):
    submit(client, [row(pid="A", positions=[1,5]), row(pid="B", positions=[10,11,12,13,14])]); process(client)
    b = submit(client, [row("2")], "2"); process(client)
    assert result(client, b["id"], "2")[0]["status"] == "pending_review"


def test_second_local_id_in_group(client):
    submit(client, [row()]); process(client)
    submit(client, [row("2")], "2"); process(client)
    second = submit(client, [row(pid="different-local")]); process(client)
    assert result(client, second["id"])[0]["status"] == "pending_review"
    with client.app.state.sessions() as db:
        assert db.scalar(select(ReviewCase.reason)) == "second_local_id_from_same_hospital"


def test_conflicting_update_keeps_link_and_matching_version(client):
    a = submit(client, [row()]); process(client)
    submit(client, [row("2")], "2"); process(client)
    with client.app.state.sessions() as db:
        old = db.scalar(select(HospitalRecord).where(HospitalRecord.site_id == "1")).matching_version_id
    changed = submit(client, [row(seed="changed")]); process(client)
    before, after = result(client, a["id"])[0], result(client, changed["id"])[0]
    assert before["global_patient_id"] == after["global_patient_id"]
    assert after["status"] == "pending_review"
    with client.app.state.sessions() as db:
        record = db.scalar(select(HospitalRecord).where(HospitalRecord.site_id == "1"))
        assert record.matching_version_id == old and record.latest_version_id != old


def test_access_control_and_data_minimisation(client):
    a = submit(client, [row()]); process(client)
    assert client.get(f"/api/v1/submissions/{a['id']}").status_code == 401
    assert client.get(f"/api/v1/submissions/{a['id']}", headers={"Authorization": "Bearer hospital-2"}).status_code == 404
    assert client.get("/api/v1/admin/overview", headers={"Authorization": "Bearer hospital-1"}).status_code == 401
    admin = {"Authorization": "Bearer admin-test"}
    assert client.get("/api/v1/admin/overview", headers=admin).json()["patients"] == 1
    patients = client.get("/api/v1/admin/patients?q=001", headers=admin).json()
    assert patients["total"] == 1
    pid = patients["items"][0]["id"]
    detail = client.get(f"/api/v1/admin/patients/{pid}", headers=admin).json()
    rid = detail["members"][0]["id"]
    history = client.get(f"/api/v1/admin/records/{rid}/versions", headers=admin)
    assert history.status_code == 200 and row()["token1"] not in history.text
    assert "candidate" not in str(result(client, a["id"]))


def test_rejections_are_persisted_and_atomic(client):
    rejected = submit(client, [row(), row(seed="different")], expected=422)
    assert "token" not in rejected["detail"]
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(HospitalRecord)) == 0
        assert db.scalar(select(Submission.status)) == "rejected"
    submit(client, [row("2")], expected=422)


def test_queue_survives_app_restart(client):
    a = submit(client, [row()])
    with TestClient(create_app(config(), start_worker=False)) as restarted:
        process(restarted)
        assert result(restarted, a["id"])[0]["global_patient_id"]


def test_failed_transaction_can_be_retried(client, monkeypatch):
    from src.broker import matching
    original = matching.process_record
    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated crash")
    with monkeypatch.context() as m:
        m.setattr(matching, "process_record", fail_after_write)
        a = submit(client, [row()], key="retry-me")
        process(client)
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(HospitalRecord)) == 0
        assert db.scalar(select(func.count()).select_from(Membership)) == 0
    retry = submit(client, [row()], key="retry-me")
    assert retry["id"] == a["id"]
    process(client)
    assert result(client, a["id"])[0]["global_patient_id"]


def test_concurrent_retry_has_one_submission(client):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: submit(client, [row()], key="concurrent"), range(4)))
    assert len({r["id"] for r in replies}) == 1
    process(client)
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(RecordVersion)) == 1


# Record-wise JSON transport feeds the same matching worker and result APIs.
def record_headers(records, key, index, site="1", digest=None):
    from src.broker.record_transport import record_digest
    return {"Authorization": f"Bearer hospital-{site}", "Idempotency-Key": key,
            "X-Submission-Digest": digest or record_digest(records),
            "X-Record-Index": str(index), "X-Token-Index": str(index % 14 + 1),
            "X-Token-Count": str(len(records)), "X-Original-Filename": "hospital1.csv",
            "X-Token-Version": "v4", "X-Key-ID": "test-key", "X-Method": "hmac_sha256"}


def json_tokens(pid="000123"):
    return [{"hospitalID": "hospital1", "patientID": pid, "token": "a" * 64 if i == 0 else ""} for i in range(14)]


def test_json_records_are_durable_idempotent_and_only_match_when_complete(database, client):
    records, key = json_tokens(), uuid.uuid4().hex
    sid = None
    # A first receiver restart must preserve every partial token, including blanks.
    with TestClient(create_app(config(), start_worker=False)) as client:
        for index in reversed(range(12)):
            response = client.post("/api/v1/submissions/records", json=records[index], headers=record_headers(records, key, index))
            assert response.status_code == 202, response.text
            assert response.json()["status"] == "receiving"
            sid = response.json()["id"]
        assert not process_next(client.app.state.sessions)
        conflict = {**records[0], "token": "b" * 64}
        assert client.post("/api/v1/submissions/records", json=conflict, headers=record_headers(records, key, 0)).status_code == 409
    with TestClient(create_app(config(), start_worker=False)) as client:
        for index in range(14):
            response = client.post("/api/v1/submissions/records", json=records[index], headers=record_headers(records, key, index))
            assert response.status_code == 202, response.text
            assert response.json()["id"] == sid
        assert response.json()["received_count"] == 14
        assert response.json()["status"] == "queued"
        assert process_next(client.app.state.sessions)
        replay = client.post("/api/v1/submissions/records", json=records[0], headers=record_headers(records, key, 0))
        assert replay.json()["status"] == "processed"
        result = client.get(f"/api/v1/submissions/{sid}/results", headers={"Authorization": "Bearer hospital-1"}).json()
        assert result["total"] == 1
        assert result["items"][0]["local_patient_id"] == "000123"
        assert result["items"][0]["global_patient_id"]
        with client.app.state.sessions() as db:
            from src.broker.models import IncomingToken
            staged = list(db.scalars(select(IncomingToken).where(IncomingToken.submission_id == uuid.UUID(sid))))
            assert len(staged) == 14 and sum(item.value == "" for item in staged) == 13


def test_json_record_rejects_identity_metadata_and_changed_content(database, client):
    records, key = json_tokens(), uuid.uuid4().hex
    with TestClient(create_app(config(), start_worker=False)) as client:
        endpoint = "/api/v1/submissions/records"
        spoof = {**records[0], "hospitalID": "hospital2"}
        assert client.post(endpoint, json=spoof, headers=record_headers(records, key, 0)).status_code == 403
        numeric = {**records[0], "patientID": 123}
        assert client.post(endpoint, json=numeric, headers=record_headers(records, key, 0)).status_code == 422
        assert client.post(endpoint, json={**records[0], "raw_name": "forbidden"}, headers=record_headers(records, key, 0)).status_code == 422
        headers = record_headers(records, key, 0)
        headers["X-Token-Count"] = "13"
        assert client.post(endpoint, json=records[0], headers=headers).status_code == 422
        headers = record_headers(records, key, 0)
        headers["X-Token-Index"] = "2"
        assert client.post(endpoint, json=records[0], headers=headers).status_code == 422
        assert client.post(endpoint, json=records[0], headers=record_headers(records, key, 0)).status_code == 202
        headers = record_headers(records, key, 1)
        headers["X-Original-Filename"] = "different.csv"
        assert client.post(endpoint, json=records[1], headers=headers).status_code == 409
        assert client.post(endpoint, json=records[1], headers=record_headers(records, key, 1, digest="0" * 64)).status_code == 409


def test_json_transfer_digest_is_checked_before_matching(database, client):
    records, key = json_tokens(), uuid.uuid4().hex
    with TestClient(create_app(config(), start_worker=False)) as client:
        for index in range(14):
            response = client.post("/api/v1/submissions/records", json=records[index],
                                   headers=record_headers(records, key, index, digest="0" * 64))
        assert response.status_code == 422
        assert response.json()["submission"]["status"] == "rejected"
        assert not process_next(client.app.state.sessions)

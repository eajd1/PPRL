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
    positions = set(range(1, 21)) if positions is None else set(positions)
    return {"site_id": site, "local_patient_id": pid, "token_version": "v3", "key_id": "test-key", "method": "hmac_sha256",
            **{f"token{i}": hashlib.sha256(f"{seed}:{i}".encode()).hexdigest() if i in positions else "" for i in range(1, 21)}}


def csv_bytes(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def config():
    return Settings(database_url=os.environ.get("BROKER_TEST_DATABASE_URL", ""), admin_hash=credential_hash("admin-test"),
                    hospitals={s: credential_hash(f"hospital-{s}") for s in ("1", "2", "3")}, key_ids=("test-key",))


def as_version(r):
    return SimpleNamespace(tokens=[r[f"token{i}"] for i in range(1, 21)], **{k: r[k] for k in ("key_id", "method", "token_version")})


def test_missing_fields_are_not_hashed():
    assert all(not v for v in build_combination_strings({}).values())
    assert build_combination_strings({"first_name": "a", "last_name": "b", "fake_medicare_id": "1234567890"})["first_last_fake_medicare_id"]


def test_group_caps_and_thresholds():
    scorer = WeightedScorer()
    assert scorer.score(as_version(row()), as_version(row()))["score"] == 24
    for positions, expected, classification in [([19, 20], 6, "review"), ([1, 5], 9, "review"),
                                                ([1, 5, 8], 12, "match"), ([1, 2, 3, 4], 4, "below_threshold"),
                                                ([1, 5, 15], 11, "review"), ([5], 5, "below_threshold"), ([], 0, "below_threshold")]:
        result = scorer.score(as_version(row()), as_version(row(positions=positions)))
        assert (result["score"], result["classification"]) == (expected, classification)
    other = row(); other["key_id"] = "different"
    assert scorer.score(as_version(row()), as_version(other))["score"] == 0


def test_validation_and_duplicate_handling():
    settings = config()
    rows, count = validate_csv(csv_bytes([row(), row()]), "1", settings)
    assert count == 1 and rows[0]["local_patient_id"] == "001"
    for bad in [row(site="2"), row(pid=""), {**row(), "token1": "invalid"}, {**row(), "token_version": "v2"}]:
        with pytest.raises(ValidationError):
            validate_csv(csv_bytes([bad]), "1", settings)
    with pytest.raises(ValidationError):
        validate_csv(csv_bytes([row(), row(seed="different")]), "1", settings)
    with pytest.raises(ValidationError):
        validate_csv(csv_bytes([row()]).replace(b"token20", b"token19"), "1", settings)
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


def test_review_range_and_blank_records(client):
    submit(client, [row()]); process(client)
    b = submit(client, [row("2", positions=[19])], "2"); process(client)
    assert result(client, b["id"], "2")[0]["global_patient_id"] is None
    c = submit(client, [row("3", positions=[])], "3"); process(client)
    r = result(client, c["id"], "3")[0]
    assert r["global_patient_id"] and r["status"] == "insufficient_evidence"


def test_transitive_chain_does_not_merge(client):
    submit(client, [row(positions=range(1, 12))]); process(client)
    submit(client, [row("2")], "2"); process(client)
    c = submit(client, [row("3", positions=range(12, 21))], "3"); process(client)
    assert result(client, c["id"], "3")[0]["status"] == "pending_review"
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(GlobalPatient)) == 1


def test_ambiguity_and_same_hospital_second_id(client):
    submit(client, [row(pid="A", positions=range(1, 12)), row(pid="B", positions=range(12, 21))]); process(client)
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

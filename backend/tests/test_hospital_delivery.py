import json
import csv
from pathlib import Path

import httpx
from src.hospital import broker_client


def test_lost_response_reuses_submission_identifier(tmp_path, monkeypatch):
    monkeypatch.setenv("PPRL_BROKER_URL", "https://broker.test")
    monkeypatch.setenv("PPRL_BROKER_CREDENTIALS", json.dumps({"1": "service-secret"}))
    tokens = tmp_path / "tokens.csv"
    row = {"site_id": "1", "local_patient_id": "000123", "token_version": "v4",
           "key_id": "test-key", "method": "hmac_sha256",
           **{f"token{i}": "a" * 64 if i == 1 else "" for i in range(1, 15)}}
    with tokens.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    state = tmp_path / "broker_delivery.json"
    seen = []
    monkeypatch.setattr(broker_client, "tls_context", lambda site: True)
    original = httpx.Client
    def handler(request):
        seen.append(request.headers["Idempotency-Key"])
        assert request.headers["Authorization"] == "Bearer service-secret"
        if len(seen) == 1:
            raise httpx.ReadTimeout("Response lost", request=request)
        assert request.url.path == "/api/v1/submissions/records"
        body = json.loads(request.content)
        index = int(request.headers["X-Record-Index"])
        assert body == {"hospitalID": "hospital1", "patientID": "000123", "token": "a" * 64 if index == 0 else ""}
        assert request.headers["Content-Type"] == "application/json"
        assert int(request.headers["X-Token-Index"]) == index + 1
        return httpx.Response(202, json={"id": "submission-id", "site_id": "1",
                              "status": "queued" if index == 13 else "receiving",
                              "expected_count": 14, "received_count": index + 1,
                              "received_at": "2026-01-01T00:00:00Z"})
    monkeypatch.setattr(broker_client.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert broker_client.send(tokens, state, "1")["status"] == "failed"
    assert broker_client.send(tokens, state, "1")["status"] == "accepted"
    assert len(seen) == 15 and len(set(seen)) == 1
    assert "service-secret" not in state.read_text()


def test_result_connection_failure_retains_saved_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PPRL_BROKER_URL", "https://broker.test")
    monkeypatch.setenv("PPRL_BROKER_CREDENTIALS", json.dumps({"1": "service-secret"}))
    path = tmp_path / "broker_delivery.json"
    broker_client.save_delivery(path, {"status": "accepted", "submission_id": "saved-id"})
    before = path.read_text()
    monkeypatch.setattr(broker_client, "tls_context", lambda site: True)
    original = httpx.Client
    def handler(request):
        raise httpx.ConnectError("Offline", request=request)
    monkeypatch.setattr(broker_client.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        broker_client.results(path, "1")
    assert exc.value.status_code == 503
    assert path.read_text() == before

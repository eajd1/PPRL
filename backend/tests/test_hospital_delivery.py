import json
from pathlib import Path

import httpx
from src.hospital import broker_client


def test_lost_response_reuses_submission_identifier(tmp_path, monkeypatch):
    monkeypatch.setenv("PPRL_BROKER_URL", "http://broker.test")
    monkeypatch.setenv("PPRL_BROKER_CREDENTIALS", json.dumps({"1": "service-secret"}))
    tokens = tmp_path / "tokens.csv"
    tokens.write_text("site_id,token_version\n1,v3\n")
    state = tmp_path / "broker_delivery.json"
    seen = []
    original = httpx.Client
    def handler(request):
        seen.append(request.headers["Idempotency-Key"])
        assert request.headers["Authorization"] == "Bearer service-secret"
        if len(seen) == 1:
            raise httpx.ReadTimeout("Response lost", request=request)
        return httpx.Response(202, json={"id": "submission-id", "status": "queued", "received_at": "2026-01-01T00:00:00Z"})
    monkeypatch.setattr(broker_client.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    assert broker_client.send(tokens, state, "1")["status"] == "failed"
    assert broker_client.send(tokens, state, "1")["status"] == "accepted"
    assert len(seen) == 2 and seen[0] == seen[1]
    assert "service-secret" not in state.read_text()


def test_result_connection_failure_retains_saved_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PPRL_BROKER_URL", "http://broker.test")
    monkeypatch.setenv("PPRL_BROKER_CREDENTIALS", json.dumps({"1": "service-secret"}))
    path = tmp_path / "broker_delivery.json"
    broker_client.save_delivery(path, {"status": "accepted", "submission_id": "saved-id"})
    before = path.read_text()
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

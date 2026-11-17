"""Token-only delivery. State is separate from hospital processing manifests."""
import csv
import hashlib
import re
import json
import os
import ssl
import threading
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException

_locks = {}
_guard = threading.Lock()


def delivery_lock(path):
    with _guard:
        return _locks.setdefault(str(path), threading.Lock())


def read_delivery(path):
    return json.loads(path.read_text()) if path.is_file() else {"status": "not_sent"}


def save_delivery(path, value):
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def configuration(site):
    url = os.environ.get("PPRL_BROKER_URL", "").rstrip("/")
    try:
        credential = json.loads(os.environ.get("PPRL_BROKER_CREDENTIALS", "{}")).get(str(site))
    except ValueError:
        credential = None
    if not url.startswith(("http://", "https://")) or not credential:
        raise HTTPException(503, "Broker delivery is not configured for this hospital")
    return url, {"Authorization": f"Bearer {credential}"}


def tls_context(site):
    """Validate the broker CA and present this hospital's certificate/private key."""
    url, _ = configuration(site)
    if not url.startswith("https://"):
        raise ValueError("Broker delivery requires HTTPS with mutual TLS")
    bundle = Path(os.environ.get("PPRL_TLS_DIR", "/run/hospital-tls")) / f"hospital{site}"
    context = ssl.create_default_context(cafile=str(bundle / "ca.crt"))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(str(bundle / "client.crt"), str(bundle / "client.key"))
    return context


def send(token_path: Path, state_path: Path, site, filename=None):
    url, headers = configuration(site)
    with delivery_lock(state_path):
        delivery = read_delivery(state_path)
        if delivery["status"] in {"processed", "accepted"} and delivery.get("submission_id"):
            return delivery
        with token_path.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        if not rows or len(rows) > 10000:
            raise HTTPException(409, "Token file is empty or exceeds the record limit")
        first = rows[0]
        records = []
        metadata = {name: first.get(name) for name in ("token_version", "key_id", "method")}
        if metadata["token_version"] != "v4" or metadata["method"] != "hmac_sha256" or not metadata["key_id"]:
            raise HTTPException(409, "Regenerate this file using the configured v4 HMAC scheme")
        for row in rows:
            if row.get("site_id") != str(site) or not row.get("local_patient_id"):
                raise HTTPException(409, "Token file hospital or patient identity is invalid")
            if any(row.get(name) != value for name, value in metadata.items()):
                raise HTTPException(409, "Token file has mixed scheme metadata")
            for position in range(1, 15):
                value = row.get(f"token{position}")
                if not isinstance(value, str) or (value and not re.fullmatch(r"[0-9a-f]{64}", value)):
                    raise HTTPException(409, "Token file has missing or invalid token columns")
                records.append({"hospitalID": f"hospital{site}", "patientID": row["local_patient_id"], "token": value})
        digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if delivery.get("transport") != "json-records-v1":
            delivery["idempotency_key"] = uuid.uuid4().hex
            delivery.pop("submission_id", None)
            delivery.pop("received_at", None)
        delivery.update(status="sending", error=None, transport="json-records-v1")
        save_delivery(state_path, delivery)
        request_headers = {**headers, "Idempotency-Key": delivery["idempotency_key"],
                           "X-Submission-Digest": digest, "X-Token-Count": str(len(records)),
                           "X-Original-Filename": filename or token_path.name,
                           "X-Token-Version": metadata["token_version"], "X-Key-ID": metadata["key_id"],
                           "X-Method": metadata["method"]}
        try:
            with httpx.Client(timeout=30, trust_env=False, verify=tls_context(site)) as client:
                for index, record in enumerate(records):
                    response = client.post(f"{url}/api/v1/submissions/records",
                                           headers={**request_headers, "X-Record-Index": str(index),
                                                    "X-Token-Index": str(index % 14 + 1)}, json=record)
                    if response.status_code >= 400:
                        detail = response.json().get("detail", "Broker rejected this record")
                        raise ValueError(detail if isinstance(detail, str) else "Broker rejected this record")
                    body = response.json()
                    if body.get("site_id") != str(site) or body.get("expected_count") != len(records):
                        raise ValueError("Invalid broker acknowledgement")
                    if delivery.get("submission_id") and delivery["submission_id"] != body["id"]:
                        raise ValueError("Broker changed the submission identity")
                    delivery.update(submission_id=body["id"], received_at=body["received_at"],
                                    received_count=body["received_count"], expected_count=len(records))
                    save_delivery(state_path, delivery)
                if body["status"] not in {"queued", "processed"} or body["received_count"] != len(records):
                    raise ValueError("Broker has not acknowledged all records")
                delivery.update(status="processed" if body["status"] == "processed" else "accepted")
        except (httpx.HTTPError, ValueError, KeyError):
            delivery.update(status="failed", error="Record delivery interrupted or rejected. Retry resumes the same saved submission.")
        save_delivery(state_path, delivery)
        return delivery


_active_sends = set()


def queue_send(token_path, state_path, site, filename=None):
    """Return promptly so record-wise transfer works across slower networks."""
    configuration(site)
    with _guard:
        delivery = read_delivery(state_path)
        if str(state_path) in _active_sends or delivery["status"] in {"accepted", "processed"}:
            return {**delivery, "status": "accepted" if delivery["status"] == "sending" else delivery["status"]}
        _active_sends.add(str(state_path))
        delivery.update(status="sending", error=None)
        save_delivery(state_path, delivery)

    def run():
        try:
            send(token_path, state_path, site, filename)
        except Exception:
            state = read_delivery(state_path)
            state.update(status="failed", error="Record delivery failed. Check configuration and retry.")
            save_delivery(state_path, state)
        finally:
            with _guard:
                _active_sends.discard(str(state_path))

    threading.Thread(target=run, daemon=True, name="broker-record-delivery").start()
    return {**delivery, "status": "accepted"}


def results(state_path: Path, site, offset=0, limit=20):
    url, headers = configuration(site)
    snapshot = read_delivery(state_path)
    if snapshot["status"] == "sending":
        with _guard:
            active = str(state_path) in _active_sends
        if active:
            return {"delivery": {**snapshot, "status": "accepted"}, "items": [], "total": 0, "offset": offset, "limit": limit}
        snapshot = read_delivery(state_path)
        if snapshot["status"] == "sending":
            snapshot.update(status="failed", error="Record delivery was interrupted. Retry resumes the saved submission.")
            save_delivery(state_path, snapshot)
    with delivery_lock(state_path):
        delivery = read_delivery(state_path)
        if not delivery.get("submission_id"):
            return {"delivery": delivery, "items": [], "total": 0, "offset": offset, "limit": limit}
        try:
            with httpx.Client(timeout=15, trust_env=False, verify=tls_context(site)) as client:
                status = client.get(f"{url}/api/v1/submissions/{delivery['submission_id']}", headers=headers)
                status.raise_for_status()
                body = status.json()
                response = client.get(f"{url}/api/v1/submissions/{delivery['submission_id']}/results",
                                      params={"offset": offset, "limit": limit}, headers=headers)
                response.raise_for_status()
                payload = response.json()
            delivery.update(status={"processed": "processed", "failed": "failed", "rejected": "failed", "receiving": "failed"}.get(body["status"], "accepted"), error=body.get("error") or ("Record delivery is incomplete. Retry resumes the saved submission." if body["status"] == "receiving" else None))
            save_delivery(state_path, delivery)
            return {**payload, "delivery": delivery}
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(503, "Broker unavailable. Saved delivery details are retained; try refreshing again.") from None

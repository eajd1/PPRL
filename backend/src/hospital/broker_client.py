"""Token-only delivery. State is separate from hospital processing manifests."""
import csv
import json
import os
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


def send(token_path: Path, state_path: Path, site):
    url, headers = configuration(site)
    with delivery_lock(state_path):
        delivery = read_delivery(state_path)
        if delivery["status"] == "processed":
            return delivery
        with token_path.open(encoding="utf-8-sig", newline="") as source:
            first = next(csv.DictReader(source), {})
        if first.get("site_id") != str(site):
            raise HTTPException(409, "Token file hospital does not match the processed file")
        if first.get("token_version") != "v3":
            raise HTTPException(409, "Regenerate this file with token version v3 before sending")
        delivery.setdefault("idempotency_key", uuid.uuid4().hex)
        delivery.update(status="sending", error=None)
        save_delivery(state_path, delivery)
        try:
            with token_path.open("rb") as source, httpx.Client(timeout=30, trust_env=False) as client:
                response = client.post(f"{url}/api/v1/submissions", headers={**headers, "Idempotency-Key": delivery["idempotency_key"]},
                                       files={"file": ("tokens.csv", source, "text/csv")})
            if response.status_code >= 400:
                detail = response.json().get("detail", "Broker rejected this submission")
                delivery.update(status="failed", error=detail if isinstance(detail, str) else "Broker rejected this submission")
            else:
                body = response.json()
                delivery.update(status="processed" if body["status"] == "processed" else "accepted",
                                submission_id=body["id"], received_at=body["received_at"])
        except (httpx.HTTPError, ValueError, KeyError):
            delivery.update(status="failed", error="Broker connection interrupted. Retry to check or resend the saved submission.")
        save_delivery(state_path, delivery)
        return delivery


def results(state_path: Path, site, offset=0, limit=20):
    url, headers = configuration(site)
    with delivery_lock(state_path):
        delivery = read_delivery(state_path)
        if not delivery.get("submission_id"):
            return {"delivery": delivery, "items": [], "total": 0, "offset": offset, "limit": limit}
        try:
            with httpx.Client(timeout=15, trust_env=False) as client:
                status = client.get(f"{url}/api/v1/submissions/{delivery['submission_id']}", headers=headers)
                status.raise_for_status()
                body = status.json()
                response = client.get(f"{url}/api/v1/submissions/{delivery['submission_id']}/results",
                                      params={"offset": offset, "limit": limit}, headers=headers)
                response.raise_for_status()
                payload = response.json()
            delivery.update(status={"processed": "processed", "failed": "failed", "rejected": "failed"}.get(body["status"], "accepted"), error=body.get("error"))
            save_delivery(state_path, delivery)
            return {**payload, "delivery": delivery}
        except (httpx.HTTPError, ValueError, KeyError):
            raise HTTPException(503, "Broker unavailable. Saved delivery details are retained; try refreshing again.") from None

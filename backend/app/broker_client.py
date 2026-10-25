"""Send individual hospital ID / local patient ID / HMAC token records over mTLS."""
from __future__ import annotations

import csv
import http.client
import json
import os
import re
import ssl
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def encode_record(hospital_id: str, record: dict) -> bytes:
    """Exactly one protected token per request, with its hospital and patient IDs."""
    return json.dumps({"hospitalID": hospital_id, "patientID": record["patient_id"],
                       "token": record["token"]}, separators=(",", ":")).encode("utf-8")


def make_payload(token_file: Path, filename: str, submission_id: str) -> dict:
    with token_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = [f"token{i}" for i in range(1, 21)]
        if not set(columns + ["local_patient_id", "method", "key_id", "token_version"]).issubset(reader.fieldnames or []):
            raise ValueError("Unexpected token output format")
        records, metadata = [], None
        for row in reader:
            current = {key: row[key] for key in ("method", "key_id", "token_version")}
            if metadata is not None and metadata != current:
                raise ValueError("Mixed token metadata")
            metadata = current
            patient_id = row["local_patient_id"]
            if not patient_id or not re.fullmatch(r"[^\r\n\x00]{1,128}", patient_id):
                raise ValueError("Invalid local patient ID")
            values = [row[key] for key in columns]
            if any(value is None or (value and not re.fullmatch(r"[0-9a-f]{64}", value)) for value in values):
                raise ValueError("Invalid protected token output; refusing transmission")
            for token_index, value in enumerate(values, start=1):
                records.append({"patient_id": patient_id, "token": value, "token_index": token_index})
        if not records:
            raise ValueError("No patient records to send")
    return {"submission_id": submission_id, "hospital_id": os.environ["HOSPITAL_ID"],
            "filename": filename, **metadata, "records": records}


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=os.environ["BROKER_CA_FILE"])
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(os.environ["HOSPITAL_CERT_FILE"], os.environ["HOSPITAL_KEY_FILE"])
    return context


def send_payload(payload: dict, *, url: str | None = None,
                 context: ssl.SSLContext | None = None) -> dict:
    target = urlsplit(url or os.environ["BROKER_URL"])
    if target.scheme != "https" or not target.hostname or target.username or target.password or target.query or target.fragment or target.path not in ("", "/"):
        raise ValueError("BROKER_URL must be an HTTPS origin, without a path or credentials")
    context = context or tls_context()
    if not payload["records"]:
        raise ValueError("No patient records to send")
    connection = None
    try:
        for index, record in enumerate(payload["records"]):
            body = encode_record(payload["hospital_id"], record)
            headers = {"Content-Type": "application/json",
                       "X-Submission-ID": payload["submission_id"], "X-Original-Filename": payload["filename"],
                       "X-Token-Version": payload["token_version"], "X-Key-ID": payload["key_id"],
                       "X-Method": payload["method"], "X-Token-Count": str(len(payload["records"])),
                       "X-Record-Index": str(index), "X-Token-Index": str(record["token_index"])}
            for attempt in range(3):
                try:
                    if connection is None:
                        connection = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=30, context=context)
                    connection.request("POST", "/api/submissions", body, headers)
                    response = connection.getresponse()
                    data = response.read(65537)
                    if response.status not in (200, 201):
                        raise ValueError(f"Broker rejected token (HTTP {response.status})")
                    acknowledgement = json.loads(data)
                    if (acknowledgement.get("submission_id") != payload["submission_id"] or
                            acknowledgement.get("hospital_id") != payload["hospital_id"] or
                            acknowledgement.get("status") not in {"receiving", "received"}):
                        raise ValueError("Invalid broker acknowledgement")
                    break
                except ssl.SSLError:
                    raise ValueError("Broker TLS certificate verification or mTLS handshake failed") from None
                except (OSError, http.client.HTTPException):
                    connection.close()
                    connection = None
                    if attempt == 2:
                        raise ValueError("Broker unreachable; saved tokens can be retried.") from None
                    time.sleep(attempt + 1)
        if acknowledgement["status"] != "received" or acknowledgement.get("record_count") != len(payload["records"]):
            raise ValueError("Broker has not acknowledged every token")
        return acknowledgement
    finally:
        if connection is not None:
            connection.close()


def submit(token_file: Path, filename: str, job_id: str, file_id: str) -> dict:
    submission_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pprl:{os.environ['HOSPITAL_ID']}:{job_id}:{file_id}"))
    return send_payload(make_payload(token_file, filename, submission_id))

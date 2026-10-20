"""Broker API. Only the trusted Nginx proxy may connect to its Unix socket."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import unquote
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DATA_DIR = Path(os.environ.get("BROKER_DATA_DIR", Path(__file__).resolve().parents[1] / "data" / "received"))
ALLOWLIST = Path(os.environ.get("BROKER_ALLOWLIST", "/run/config/allowlist.json"))
STORAGE_LOCK = threading.Lock()  # The broker runs exactly one Uvicorn worker.
TOKEN_COLUMNS = ["hospitalID", "patientID", "token"]
SUBMISSION_COLUMNS = ["submission_id", "hospital_id", "filename", "token_version", "key_id", "method"]
METADATA_COLUMNS = [*SUBMISSION_COLUMNS, "received_at", "status", "record_count", "token_count"]


def read_metadata(directory: Path) -> dict:
    with (directory / "metadata.csv").open(newline="", encoding="utf-8") as handle:
        metadata = next(csv.DictReader(handle))
    metadata["record_count"] = int(metadata["record_count"])
    if "token_count" in metadata:
        metadata["token_count"] = int(metadata["token_count"])
    return metadata


def write_csv(path: Path, columns: list[str], rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


@asynccontextmanager
async def lifespan(app):
    entries = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    if not entries or any(not re.fullmatch(r"[0-9a-f]{64}", k) or v not in {"hospital1", "hospital2", "hospital3"} for k, v in entries.items()):
        raise RuntimeError("Invalid certificate allowlist")
    app.state.allowlist = entries
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="PPRL Broker", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def hospital(request: Request) -> str:
    if request.headers.get("x-proxy-role") != "hospital" or request.headers.get("x-client-verify") != "SUCCESS":
        raise HTTPException(403, "Verified client certificate required")
    try:
        cert = x509.load_pem_x509_certificate(unquote(request.headers.get("x-client-cert", "")).encode())
        identity = request.app.state.allowlist.get(cert.fingerprint(hashes.SHA256()).hex())
    except ValueError:
        identity = None
    if identity is None:
        raise HTTPException(403, "Certificate is not authorised")
    return identity


def operator(request: Request):
    if request.headers.get("x-proxy-role") != "operator":
        raise HTTPException(403, "Operator login required")


class TokenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hospitalID: Literal["hospital1", "hospital2", "hospital3"]
    patientID: str = Field(pattern=r"^[^\r\n\x00]{1,128}$")
    token: str = Field(pattern=r"^[0-9a-f]{64}$")


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: UUID
    filename: str = Field(pattern=r"(?i)^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}\.csv$")
    token_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    key_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    method: Literal["hmac_sha256"]
    token_count: int = Field(ge=1, le=2000000)
    record_index: int = Field(ge=0)
    token_index: int = Field(ge=1, le=20)
    record: TokenRecord


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": "Invalid token submission structure"})


async def csv_submission(request: Request) -> Submission:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/csv":
        raise HTTPException(415, "Submit one protected token as text/csv")
    try:
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(413, "One token per request")
        reader = csv.DictReader(io.StringIO(body.decode("utf-8"), newline=""), strict=True)
        if reader.fieldnames != TOKEN_COLUMNS:
            raise ValueError("Unexpected CSV columns")
        rows = list(reader)
        if len(rows) != 1 or None in rows[0] or any(value is None for value in rows[0].values()):
            raise ValueError("Exactly one token record required")
        metadata = {key: request.headers.get("x-" + key.replace("_", "-")) for key in
                    ("submission_id", "token_version", "key_id", "method", "token_count", "record_index", "token_index")}
        metadata["filename"] = request.headers.get("x-original-filename")
        payload = Submission.model_validate({**metadata, "record": rows[0]})
        if payload.record_index >= payload.token_count:
            raise ValueError("Invalid token index")
        return payload
    except (ValueError, csv.Error, ValidationError):
        raise HTTPException(422, "Invalid protected token CSV or metadata") from None


def atomic_csv(path: Path, columns: list[str], rows):
    descriptor, name = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        write_csv(temporary, columns, rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@app.post("/api/submissions")
def receive(response: Response, identity: str = Depends(hospital), payload: Submission = Depends(csv_submission)):
    if payload.record.hospitalID != identity:
        raise HTTPException(403, "Hospital identity does not match certificate")
    sid = str(payload.submission_id)
    expected = {key: getattr(payload, key) for key in SUBMISSION_COLUMNS if key != "hospital_id"}
    expected.update(submission_id=sid, hospital_id=identity, token_count=payload.token_count)
    with STORAGE_LOCK:
        directory = DATA_DIR / sid
        if directory.exists():
            metadata = read_metadata(directory)
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise HTTPException(409, "Submission metadata conflicts with existing receipt")
        else:
            metadata = {**expected, "received_at": datetime.now(timezone.utc).isoformat(),
                        "status": "receiving", "record_count": 0}
            with tempfile.TemporaryDirectory(prefix=".pending-", dir=DATA_DIR) as staging:
                staged = Path(staging) / sid
                (staged / "tokens").mkdir(parents=True)
                write_csv(staged / "metadata.csv", METADATA_COLUMNS, [metadata])
                staged.rename(directory)
        tokens = directory / "tokens"
        filename = f"{payload.record_index:08d}-{payload.token_index:02d}.csv"
        existing = next(tokens.glob(f"{payload.record_index:08d}-*.csv"), None)
        row = payload.record.model_dump()
        if existing is not None:
            with existing.open(newline="", encoding="utf-8") as handle:
                previous = next(csv.DictReader(handle))
            if existing.name != filename or previous != row:
                raise HTTPException(409, "Token index already used for different content")
            response.status_code = 200
        else:
            atomic_csv(tokens / filename, TOKEN_COLUMNS, [row])
            response.status_code = 201
        # Counting saved files also recovers after a crash between token and receipt writes.
        metadata["record_count"] = sum(1 for _ in tokens.glob("*.csv"))
        metadata["status"] = "received" if metadata["record_count"] == metadata["token_count"] else "receiving"
        atomic_csv(directory / "metadata.csv", METADATA_COLUMNS, [metadata])
    return {key: metadata[key] for key in ("submission_id", "hospital_id", "filename", "received_at", "status", "record_count", "token_count")}


@app.get("/api/submissions", dependencies=[Depends(operator)])
def submissions():
    entries = []
    for directory in DATA_DIR.iterdir():
        if directory.is_dir() and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", directory.name):
            metadata = read_metadata(directory)
            entries.append({key: metadata[key] for key in ("submission_id", "hospital_id", "filename", "received_at", "status", "record_count")})
    return sorted(entries, key=lambda entry: entry["received_at"], reverse=True)[:500]


@app.get("/api/health")
def health(request: Request):
    if request.headers.get("x-proxy-role") == "operator":
        operator(request)
    else:
        hospital(request)
    return {"status": "ok"}

"""Broker API. Only the trusted Nginx proxy may connect to its Unix socket."""
from __future__ import annotations

import csv
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
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DATA_DIR = Path(os.environ.get("BROKER_DATA_DIR", Path(__file__).resolve().parents[1] / "data" / "received"))
ALLOWLIST = Path(os.environ.get("BROKER_ALLOWLIST", "/run/config/allowlist.json"))
STORAGE_LOCK = threading.Lock()  # The broker runs exactly one Uvicorn worker.
TOKEN_COLUMNS = ["hospitalID", "patientID", "token"]
SUBMISSION_COLUMNS = ["submission_id", "hospital_id", "filename", "token_version", "key_id", "method"]
METADATA_COLUMNS = [*SUBMISSION_COLUMNS, "received_at", "status", "record_count", "token_count"]


HOSPITALS = ("hospital1", "hospital2", "hospital3")
STORED_COLUMNS = [*TOKEN_COLUMNS, *[key for key in SUBMISSION_COLUMNS if key != "hospital_id"],
                  "received_at", "token_count", "record_index", "token_index"]


def write_csv(path: Path, columns: list[str], rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())


class RecordStore:
    """One durable CSV per hospital, with in-memory receipt and sorted-list indexes."""

    def __init__(self):
        self.rows = {}
        self.receipts = {}
        self.sorted_rows = None
        for hospital_id in HOSPITALS:
            path = DATA_DIR / f"{hospital_id}.csv"
            if path.exists():
                with path.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        self.index(row)
        self.migrate_legacy()

    def index(self, row):
        row = dict(row)
        for key in ("token_count", "record_index", "token_index"):
            row[key] = int(row[key])
        key = (row["submission_id"], row["record_index"])
        if key in self.rows:
            if self.rows[key] != row:
                raise ValueError("Conflicting stored record")
            return
        self.rows[key] = row
        sid = row["submission_id"]
        receipt = self.receipts.setdefault(sid, {
            **{key: row[key] for key in SUBMISSION_COLUMNS if key != "hospital_id"},
            "hospital_id": row["hospitalID"], "received_at": row["received_at"],
            "token_count": row["token_count"], "record_count": 0,
        })
        receipt["record_count"] += 1
        receipt["status"] = "received" if receipt["record_count"] == receipt["token_count"] else "receiving"
        self.sorted_rows = None

    def migrate_legacy(self):
        legacy = [directory for directory in DATA_DIR.iterdir()
                  if directory.is_dir() and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", directory.name)]
        if not legacy:
            return
        for directory in legacy:
            with (directory / "metadata.csv").open(newline="", encoding="utf-8") as handle:
                metadata = next(csv.DictReader(handle))
            for path in sorted((directory / "tokens").glob("*.csv")):
                with path.open(newline="", encoding="utf-8") as handle:
                    record = next(csv.DictReader(handle))
                record.update({key: metadata[key] for key in SUBMISSION_COLUMNS if key != "hospital_id"})
                record.update(received_at=metadata["received_at"], token_count=metadata["token_count"],
                              record_index=int(path.stem.split("-")[0]), token_index=int(path.stem.split("-")[1]))
                self.index(record)
        for hospital_id in HOSPITALS:
            rows = [row for row in self.rows.values() if row["hospitalID"] == hospital_id]
            if rows:
                atomic_csv(DATA_DIR / f"{hospital_id}.csv", STORED_COLUMNS, rows)
        # Preserve the old files outside active storage after the consolidated files commit.
        archive = DATA_DIR.parent / "legacy-records"
        archive.mkdir(exist_ok=True)
        for directory in legacy:
            target = archive / directory.name
            if directory.resolve().parent != DATA_DIR.resolve() or target.resolve().parent != archive.resolve():
                raise ValueError("Invalid migration path")
            directory.rename(target)

    def append(self, row):
        path = DATA_DIR / f"{row['hospitalID']}.csv"
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=STORED_COLUMNS, lineterminator="\n")
            if handle.tell() == 0:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        self.index(row)

    def ordered(self):
        if self.sorted_rows is None:
            self.sorted_rows = sorted(self.rows.values(), key=lambda row: (
                row["hospitalID"],
                tuple((0, int(part)) if part.isascii() and part.isdigit() else (1, part.casefold())
                      for part in re.split(r"([0-9]+)", row["patientID"]) if part),
                row["patientID"], row["submission_id"], row["token_index"], row["record_index"],
            ))
        return self.sorted_rows


@asynccontextmanager
async def lifespan(app):
    entries = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    if not entries or any(not re.fullmatch(r"[0-9a-f]{64}", k) or v not in {"hospital1", "hospital2", "hospital3"} for k, v in entries.items()):
        raise RuntimeError("Invalid certificate allowlist")
    app.state.allowlist = entries
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app.state.store = RecordStore()
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
    token: str = Field(pattern=r"^(?:[0-9a-f]{64})?$")


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


async def record_submission(request: Request) -> Submission:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Submit one protected record as application/json")
    try:
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(413, "One token per request")
        record = json.loads(body)
        if not isinstance(record, dict):
            raise ValueError("Exactly one record required")
        metadata = {key: request.headers.get("x-" + key.replace("_", "-")) for key in
                    ("submission_id", "token_version", "key_id", "method", "token_count", "record_index", "token_index")}
        metadata["filename"] = request.headers.get("x-original-filename")
        payload = Submission.model_validate({**metadata, "record": record})
        if payload.record_index >= payload.token_count:
            raise ValueError("Invalid token index")
        return payload
    except (ValueError, csv.Error, ValidationError):
        raise HTTPException(422, "Invalid protected record or metadata") from None


def atomic_csv(path: Path, columns: list[str], rows):
    descriptor, name = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        write_csv(temporary, columns, rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def process_received_records() -> None:
    # To Do. Implement PSI and the remaining broker functionality here.
    pass


@app.post("/api/submissions")
def receive(request: Request, response: Response, identity: str = Depends(hospital), payload: Submission = Depends(record_submission)):
    if payload.record.hospitalID != identity:
        raise HTTPException(403, "Hospital identity does not match certificate")
    sid = str(payload.submission_id)
    expected = {key: getattr(payload, key) for key in SUBMISSION_COLUMNS if key != "hospital_id"}
    expected.update(submission_id=sid, hospital_id=identity, token_count=payload.token_count)
    with STORAGE_LOCK:
        store = request.app.state.store
        metadata = store.receipts.get(sid)
        if metadata and any(metadata.get(key) != value for key, value in expected.items()):
            raise HTTPException(409, "Submission metadata conflicts with existing receipt")
        row = {**payload.record.model_dump(),
               **{key: value for key, value in expected.items() if key != "hospital_id"},
               "received_at": metadata["received_at"] if metadata else datetime.now(timezone.utc).isoformat(),
               "record_index": payload.record_index, "token_index": payload.token_index}
        previous = store.rows.get((sid, payload.record_index))
        if previous is not None:
            if previous != row:
                raise HTTPException(409, "Token index already used for different content")
            response.status_code = 200
        else:
            store.append(row)
            response.status_code = 201
        metadata = store.receipts[sid]
        return {key: metadata[key] for key in ("submission_id", "hospital_id", "filename", "received_at", "status", "record_count", "token_count")}


@app.get("/api/submissions", dependencies=[Depends(operator)])
def submissions(request: Request):
    with STORAGE_LOCK:
        return sorted(request.app.state.store.receipts.values(), key=lambda entry: entry["received_at"], reverse=True)[:500]


@app.get("/api/records", dependencies=[Depends(operator)])
def records(request: Request, hospital_id: Literal["hospital1", "hospital2", "hospital3"] | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    with STORAGE_LOCK:
        entries = request.app.state.store.ordered()
        if hospital_id:
            entries = [row for row in entries if row["hospitalID"] == hospital_id]
        columns = [*TOKEN_COLUMNS, "submission_id", "received_at", "filename", "token_index"]
        page = [{key: row[key] for key in columns} for row in entries[offset:offset + limit]]
        return {"records": page, "total": len(entries), "offset": offset, "limit": limit}


@app.get("/api/health")
def health(request: Request):
    if request.headers.get("x-proxy-role") == "operator":
        operator(request)
    else:
        hospital(request)
    return {"status": "ok"}

"""Hospital-local upload API. No raw identifiers are transmitted to a broker."""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from src.hospital import pipeline

RUNTIME = Path(os.environ.get("PPRL_RUNTIME_DIR", Path(__file__).resolve().parents[1] / "runtime")).resolve()
MAX_FILES = 20
MAX_BYTES = 10 * 1024 * 1024
PROCESSING_SLOTS = threading.Semaphore(2)
TERMINAL = {"success", "partial_success", "failure"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_job(job: dict) -> None:
    directory = RUNTIME / job["job_id"]
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "job.tmp"
    temporary.write_text(json.dumps(job, indent=2), encoding="utf-8")
    temporary.replace(directory / "job.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline.validate_configuration()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # An interrupted local process cannot resume in-memory background work.
    for manifest in RUNTIME.glob("*/job.json"):
        job = json.loads(manifest.read_text(encoding="utf-8"))
        if job["status"] not in TERMINAL:
            for item in job["files"]:
                if item["status"] in {"queued", "processing"}:
                    item.update(status="failure", error="Processing was interrupted. Upload this file again.")
            finish_job(job)
    yield


app = FastAPI(title="Hospital PPRL API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def read_job(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "Job not found")
    path = RUNTIME / job_id / "job.json"
    if not path.is_file():
        raise HTTPException(404, "Job not found")
    return json.loads(path.read_text(encoding="utf-8"))


def find_item(job_id: str, file_id: str) -> tuple[dict, dict]:
    job = read_job(job_id)
    for item in job["files"]:
        if item["file_id"] == file_id:
            return job, item
    raise HTTPException(404, "File not found")


def artifact_path(job: dict, item: dict, kind: str) -> Path:
    if item["status"] != "success" or kind not in item.get("artifacts", {}):
        raise HTTPException(404, "Output not available")
    root = RUNTIME / job["job_id"]
    path = (root / item["artifacts"][kind]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Output not available")
    return path


def public_job(job: dict) -> dict:
    result = {k: v for k, v in job.items() if k != "files"}
    result["files"] = []
    for item in job["files"]:
        visible = {k: v for k, v in item.items() if k not in {"input_path", "artifacts"}}
        base = f"/api/jobs/{job['job_id']}/files/{item['file_id']}"
        if item["status"] == "success":
            visible["downloads"] = {kind: f"{base}/download/{kind}" for kind in item["artifacts"]}
            visible["preview_url"] = f"{base}/preview"
        result["files"].append(visible)
    return result


def finish_job(job: dict) -> None:
    succeeded = sum(item["status"] == "success" for item in job["files"])
    job.update(successful_files=succeeded, failed_files=len(job["files"]) - succeeded,
               status="success" if succeeded == len(job["files"]) else "partial_success" if succeeded else "failure",
               finished_at=now())
    save_job(job)


def process_job(job_id: str) -> None:
    with PROCESSING_SLOTS:
        job = read_job(job_id)
        job["status"] = "processing"
        save_job(job)
        for item in job["files"]:
            if item["status"] == "failure":
                continue
            item["status"] = "processing"
            save_job(job)
            root = RUNTIME / job_id
            try:
                result = pipeline.process_file(root / item["input_path"], root / "files" / item["file_id"] / "output")
                report = json.loads(Path(result["report_file"]).read_text(encoding="utf-8"))
                item.update(status="success", hospital_id=result["hospital_id"],
                            input_rows=result["input_rows"], cleaned_rows=result["cleaned_visit_rows"],
                            patients=result["patient_linkage_rows"], tokens_generated=result["tokens_generated"],
                            patients_with_no_tokens=result["patients_with_no_tokens"],
                            duplicates_removed=result["exact_duplicates_removed"],
                            rejected_rows=result["identity_rows_rejected"],
                            invalid_values=report["invalid_values_replaced_with_blank"],
                            token_version=result["token_version"], key_id=result["key_id"])
                item["artifacts"] = {
                    "tokens": str(Path(result["token_file"]).relative_to(root)),
                    "token_report": str(Path(result["token_report_file"]).relative_to(root)),
                    "cleaning_report": str(Path(result["report_file"]).relative_to(root)),
                }
            except ValueError as exc:
                message = str(exc).replace(str(root), "job folder")
                item.update(status="failure", error=message[:800])
            except Exception:
                item.update(status="failure", error="This file could not be processed. Check its format and retry.")
            save_job(job)
        finish_job(job)


@app.get("/api/health")
def health():
    return {"status": "ok", "token_version": pipeline.tokens.TOKEN_SCHEME_VERSION,
            "key_id": pipeline.tokens.TOKEN_KEY_ID, "max_files": MAX_FILES, "max_file_bytes": MAX_BYTES}


@app.post("/api/jobs", status_code=202)
async def create_job(background_tasks: BackgroundTasks, files: Annotated[list[UploadFile], File()]):
    if not 1 <= len(files) <= MAX_FILES:
        for upload in files:
            await upload.close()
        raise HTTPException(422, f"Choose between 1 and {MAX_FILES} CSV files")
    job = {"job_id": uuid.uuid4().hex, "status": "queued", "created_at": now(),
           "successful_files": 0, "failed_files": 0, "files": []}
    root = RUNTIME / job["job_id"]
    for index, upload in enumerate(files, start=1):
        name = upload.filename or ""
        item = {"file_id": f"{index:03d}", "filename": name, "status": "queued"}
        job["files"].append(item)
        path = None
        try:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,119}\.csv", name, flags=re.I):
                raise ValueError("Choose a .csv file with a simple filename (letters, digits, spaces, dots, underscores or hyphens).")
            path = root / "files" / item["file_id"] / "input" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with path.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("This file exceeds the 10 MB limit.")
                    destination.write(chunk)
            if not size:
                raise ValueError("This file is empty.")
            item.update(input_path=str(path.relative_to(root)), size_bytes=size)
        except ValueError as exc:
            item.update(status="failure", error=str(exc))
            # Remove any partial upload; it is never processed.
            if path is not None and path.is_relative_to(root / "files" / item["file_id"]) and path.is_file():
                path.unlink()
        finally:
            await upload.close()
    save_job(job)
    if any(item["status"] == "queued" for item in job["files"]):
        background_tasks.add_task(process_job, job["job_id"])
    else:
        finish_job(job)
    return public_job(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return public_job(read_job(job_id))


@app.get("/api/jobs/{job_id}/files/{file_id}/preview")
def preview(job_id: str, file_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    job, item = find_item(job_id, file_id)
    path = artifact_path(job, item, "tokens")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = []
        for index, row in enumerate(reader):
            if index < offset:
                continue
            if len(rows) >= limit:
                break
            rows.append(row)
    return {"columns": columns, "rows": rows, "offset": offset,
            "limit": limit, "total_rows": item["patients"]}


@app.get("/api/jobs/{job_id}/files/{file_id}/download/{kind}")
def download(job_id: str, file_id: str, kind: str):
    job, item = find_item(job_id, file_id)
    path = artifact_path(job, item, kind)
    return FileResponse(path, filename=path.name,
                        media_type="text/csv" if kind == "tokens" else "application/json")


@app.get("/api/jobs/{job_id}/download")
def download_all(job_id: str):
    job = read_job(job_id)
    if job["status"] not in TERMINAL:
        raise HTTPException(409, "This job is still processing")
    successful = [item for item in job["files"] if item["status"] == "success"]
    if not successful:
        raise HTTPException(404, "No token files were generated")
    archive_path = RUNTIME / job_id / "all_tokens.zip"
    if not archive_path.is_file():
        temporary = archive_path.with_name(f"archive_{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in successful:
                    path = artifact_path(job, item, "tokens")
                    archive.write(path, arcname=f"{item['file_id']}_{path.name}")
            temporary.replace(archive_path)
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(archive_path, media_type="application/zip",
                        filename=f"tokens_{job_id[:8]}.zip")

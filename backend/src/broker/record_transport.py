"""Durably collect individual JSON tokens before existing batch matching."""
import csv
import hashlib
import io
import json
import re

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from .models import AuditEvent, IncomingToken, RecordTransfer, Submission
from .validation import COLUMNS, TOKEN_COLUMNS, ValidationError, validate_csv


class TokenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    hospitalID: str = Field(min_length=1, max_length=128)
    patientID: str = Field(min_length=1, max_length=200)
    token: str = Field(pattern=r"^(?:[0-9a-f]{64})?$")


def record_digest(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def accept_record(sessions, settings, site, record, key, digest, index, position, count, filename, version, key_id, method):
    if record.hospitalID != f"hospital{site}":
        raise HTTPException(403, "Record hospital identity does not match the verified hospital")
    if not record.patientID.strip() or any(ord(c) < 32 for c in record.patientID):
        raise HTTPException(422, "Invalid patientID")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", key) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(422, "Invalid submission identifier or digest")
    if not 1 <= count <= settings.max_rows * len(TOKEN_COLUMNS) or count % len(TOKEN_COLUMNS):
        raise HTTPException(422, "Expected count must include all 14 token positions per patient")
    if not 0 <= index < count or position != index % len(TOKEN_COLUMNS) + 1:
        raise HTTPException(422, "Invalid record index or token position")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,115}\.csv", filename, re.I):
        raise HTTPException(422, "Invalid original filename")
    if version != settings.token_version or key_id not in settings.key_ids or method != "hmac_sha256":
        raise HTTPException(422, "Incompatible token version, key ID or method")
    with sessions.begin() as db:
        # Same acceptance lock as the existing CSV endpoint; persisted state survives crashes.
        db.execute(text("SELECT pg_advisory_xact_lock(72423020)"))
        submission = db.scalar(select(Submission).where(Submission.site_id == site, Submission.idempotency_key == key))
        metadata = dict(expected_count=count, filename=filename, token_version=version, key_id=key_id, method=method)
        if submission is None:
            submission = Submission(site_id=site, idempotency_key=key, digest=digest, status="receiving", payload=[])
            db.add(submission)
            db.flush()
            transfer = RecordTransfer(submission_id=submission.id, **metadata)
            db.add(transfer)
            db.flush()
        else:
            transfer = db.get(RecordTransfer, submission.id)
            if submission.digest != digest or transfer is None or any(getattr(transfer, name) != value for name, value in metadata.items()):
                raise HTTPException(409, "Submission identifier already used with different content or metadata")
        previous = db.get(IncomingToken, (submission.id, index))
        if previous:
            if (previous.patient_id, previous.token_position, previous.value) != (record.patientID, position, record.token):
                raise HTTPException(409, "Record index already used with different content")
        elif submission.status != "receiving":
            raise HTTPException(409, "Submission is already complete")
        else:
            db.add(IncomingToken(submission_id=submission.id, record_index=index,
                                 patient_id=record.patientID, token_position=position, value=record.token))
            db.flush()
        received = db.scalar(select(func.count()).select_from(IncomingToken).where(IncomingToken.submission_id == submission.id))
        if received == count and submission.status == "receiving":
            staged = list(db.scalars(select(IncomingToken).where(IncomingToken.submission_id == submission.id).order_by(IncomingToken.record_index)))
            records = [dict(hospitalID=f"hospital{site}", patientID=item.patient_id, token=item.value) for item in staged]
            error = None
            try:
                if record_digest(records) != digest:
                    raise ValidationError("Submission content digest does not match its records")
                rows = []
                for start in range(0, count, len(TOKEN_COLUMNS)):
                    group = staged[start:start + len(TOKEN_COLUMNS)]
                    if len({item.patient_id for item in group}) != 1:
                        raise ValidationError("Patient identity changed within a token group")
                    rows.append(dict(site_id=site, local_patient_id=group[0].patient_id,
                                     token_version=version, key_id=key_id, method=method,
                                     **{name: item.value for name, item in zip(TOKEN_COLUMNS, group)}))
                # Reuse the existing strict matching-input validator, entirely in memory.
                output = io.StringIO(newline="")
                writer = csv.DictWriter(output, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
                payload, duplicates = validate_csv(output.getvalue().encode(), site, settings)
                submission.payload, submission.row_count, submission.duplicate_count = payload, len(payload), duplicates
            except ValidationError as exc:
                error = str(exc)
            submission.status, submission.error = ("rejected", error) if error else ("queued", None)
            db.add(AuditEvent(submission_id=submission.id, event="submission_rejected" if error else "submission_accepted"))
        elif received == count and submission.status == "failed":
            submission.status, submission.error = "queued", None
        return {"id": str(submission.id), "site_id": site, "status": submission.status,
                "received_at": submission.received_at, "received_count": received,
                "expected_count": count, "error": submission.error}

"""Broker API and dashboard host. Every data route requires a scoped credential."""
import hashlib
import re
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.dialects.postgresql import insert

from .auth import administrator, hospital_identity
from .config import Settings
from .models import (Assessment, AuditEvent, GlobalPatient, Hospital, HospitalRecord,
                     Membership, RecordVersion, ReviewCase, Submission)
from .storage import connect
from .validation import ValidationError, validate_csv
from .worker import run_worker


def submission_view(s):
    return {key: getattr(s, key) for key in ("id", "site_id", "received_at", "completed_at", "status",
                                           "row_count", "duplicate_count", "error")}


def record_view(r):
    return {k: getattr(r, k) for k in ("id", "site_id", "local_patient_id", "status", "first_received_at",
                                      "latest_received_at", "content_changed_at")}


def assessment_view(a):
    return {"candidate_patient_id": a.candidate_patient_id, "candidate_record_id": a.candidate_record_id,
            "score": a.score, "evidence": a.evidence, "scoring_version": a.scoring_version}


def page(db, query, offset, limit, serializer):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {"items": [serializer(x) for x in db.scalars(query.offset(offset).limit(limit))],
            "total": total, "offset": offset, "limit": limit}


def create_app(settings=None, start_worker=True):
    settings = settings or Settings()
    settings.validate()
    engine, sessions = connect(settings)

    @asynccontextmanager
    async def lifespan(app):
        with sessions.begin() as db:
            existing = list(db.scalars(select(Hospital)))
            for h in existing:
                h.enabled = h.id in settings.hospitals
            for site, hashed in settings.hospitals.items():
                db.execute(insert(Hospital).values(id=site, credential_hash=hashed, enabled=True)
                           .on_conflict_do_update(index_elements=[Hospital.id], set_={"credential_hash": hashed, "enabled": True}))
        stop = threading.Event()
        worker = threading.Thread(target=run_worker, args=(sessions, stop), daemon=True, name="broker-matcher")
        if start_worker:
            worker.start()
        yield
        stop.set()
        if start_worker:
            worker.join(timeout=10)
        engine.dispose()

    app = FastAPI(title="PPRL Broker", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.sessions = settings, sessions

    @app.middleware("http")
    async def headers(request, call_next):
        length = request.headers.get("content-length")
        if request.method == "POST" and length and (not length.isdigit() or int(length) > settings.max_bytes + 65536):
            return JSONResponse({"detail": "Upload exceeds the request limit"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.get("/api/health")
    def health():
        try:
            with engine.connect() as db:
                db.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
        except Exception:
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ok"}

    @app.post("/api/v1/submissions", status_code=202)
    def submit(file: UploadFile = File(...), idempotency_key: str = Header(...), site: str = Depends(hospital_identity)):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", idempotency_key):
            raise HTTPException(422, "Invalid idempotency identifier")
        try:
            data = file.file.read(settings.max_bytes + 1)
        finally:
            file.file.close()
        if len(data) > settings.max_bytes:
            raise HTTPException(413, "Upload exceeds the file limit")
        digest = hashlib.sha256(data).hexdigest()
        error, rows, duplicates = None, [], 0
        try:
            rows, duplicates = validate_csv(data, site, settings)
        except ValidationError as exc:
            error = str(exc)
        with sessions.begin() as db:
            # Serialize acceptance so sequence order follows committed acceptance.
            db.execute(text("SELECT pg_advisory_xact_lock(72423020)"))
            previous = db.scalar(select(Submission).where(Submission.site_id == site,
                                                          Submission.idempotency_key == idempotency_key))
            if previous:
                if previous.digest != digest:
                    raise HTTPException(409, "Submission identifier already used with different content")
                if previous.status == "failed":
                    if error:
                        raise HTTPException(422, error)
                    previous.status, previous.error, previous.payload = "queued", None, rows
                response = submission_view(previous)
                rejected = previous.status == "rejected"
            else:
                s = Submission(site_id=site, idempotency_key=idempotency_key, digest=digest,
                               payload=rows, row_count=len(rows), duplicate_count=duplicates,
                               status="rejected" if error else "queued", error=error)
                db.add(s)
                db.flush()
                db.add(AuditEvent(submission_id=s.id, event="submission_rejected" if error else "submission_accepted"))
                response, rejected = submission_view(s), bool(error)
        if rejected:
            from fastapi.encoders import jsonable_encoder
            return JSONResponse({"detail": response["error"], "submission": jsonable_encoder(response)}, status_code=422)
        return response

    def owned(db, sid, site):
        s = db.scalar(select(Submission).where(Submission.id == sid, Submission.site_id == site))
        if s is None:
            raise HTTPException(404, "Submission not found")
        return s

    @app.get("/api/v1/submissions/{sid}")
    def submission(sid: uuid.UUID, site: str = Depends(hospital_identity)):
        with sessions() as db:
            return submission_view(owned(db, sid, site))

    @app.get("/api/v1/submissions/{sid}/results")
    def results(sid: uuid.UUID, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), site: str = Depends(hospital_identity)):
        with sessions() as db:
            s = owned(db, sid, site)
            return {"status": s.status, "items": s.results[offset:offset + limit], "total": len(s.results), "offset": offset, "limit": limit}

    admin = [Depends(administrator)]

    @app.get("/api/v1/admin/overview", dependencies=admin)
    def overview():
        with sessions() as db:
            counts = {name: db.scalar(select(func.count()).select_from(model)) for name, model in
                      (("patients", GlobalPatient), ("hospital_records", HospitalRecord), ("submissions", Submission), ("linked_records", Membership))}
            counts["pending_reviews"] = db.scalar(select(func.count()).select_from(ReviewCase).where(ReviewCase.status == "open"))
            counts["queued_submissions"] = db.scalar(select(func.count()).select_from(Submission).where(Submission.status == "queued"))
            return counts

    def patient_summary(db, p):
        return {"id": p.id, "first_received_at": p.first_received_at, "latest_received_at": p.latest_received_at,
                "hospital_count": db.scalar(select(func.count()).select_from(Membership).where(Membership.patient_id == p.id))}

    @app.get("/api/v1/admin/patients", dependencies=admin)
    def patients(q: str = Query("", max_length=200), offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        query = select(GlobalPatient)
        if q:
            local_matches = (select(Membership.patient_id).join(HospitalRecord, Membership.record_id == HospitalRecord.id)
                             .where(HospitalRecord.local_patient_id.contains(q, autoescape=True)))
            query = query.where(cast(GlobalPatient.id, String).contains(q, autoescape=True) | GlobalPatient.id.in_(local_matches))
        with sessions() as db:
            return page(db, query.order_by(GlobalPatient.latest_received_at.desc(), GlobalPatient.id), offset, limit, lambda p: patient_summary(db, p))

    @app.get("/api/v1/admin/patients/{pid}", dependencies=admin)
    def patient_detail(pid: uuid.UUID):
        with sessions() as db:
            p = db.get(GlobalPatient, pid)
            if p is None:
                raise HTTPException(404, "Patient not found")
            records = db.scalars(select(HospitalRecord).join(Membership, HospitalRecord.id == Membership.record_id).where(Membership.patient_id == pid))
            result = patient_summary(db, p)
            result["members"] = [record_view(r) for r in records]
            return result

    @app.get("/api/v1/admin/records/{rid}/versions", dependencies=admin)
    def history(rid: uuid.UUID, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        with sessions() as db:
            r = db.get(HospitalRecord, rid)
            if r is None:
                raise HTTPException(404, "Record not found")
            def view(v):
                return {"id": v.id, "received_at": v.received_at, "submission_id": v.submission_id,
                        "token_version": v.token_version, "key_id": v.key_id, "token_count": sum(bool(t) for t in v.tokens),
                        "active_for_matching": v.id == r.matching_version_id,
                        "assessments": [assessment_view(a) for a in db.scalars(select(Assessment).where(Assessment.version_id == v.id))]}
            return page(db, select(RecordVersion).where(RecordVersion.record_id == rid).order_by(RecordVersion.received_at.desc(), RecordVersion.id), offset, limit, view)

    @app.get("/api/v1/admin/submissions", dependencies=admin)
    def admin_submissions(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), status: str = Query("", max_length=30)):
        query = select(Submission)
        if status:
            query = query.where(Submission.status == status)
        with sessions() as db:
            return page(db, query.order_by(Submission.sequence.desc()), offset, limit, submission_view)

    @app.get("/api/v1/admin/submissions/{sid}", dependencies=admin)
    def admin_submission(sid: uuid.UUID, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        with sessions() as db:
            s = db.scalar(select(Submission).where(Submission.id == sid))
            if s is None:
                raise HTTPException(404, "Submission not found")
            return {**submission_view(s), "items": s.results[offset:offset + limit], "total": len(s.results), "offset": offset, "limit": limit}

    @app.get("/api/v1/admin/reviews", dependencies=admin)
    def reviews(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        with sessions() as db:
            def view(r):
                return {"id": r.id, "reason": r.reason, "created_at": r.created_at,
                        "record": record_view(db.get(HospitalRecord, r.record_id))}
            return page(db, select(ReviewCase).where(ReviewCase.status == "open").order_by(ReviewCase.created_at.desc(), ReviewCase.id), offset, limit, view)

    @app.get("/api/v1/admin/reviews/{rid}", dependencies=admin)
    def review_detail(rid: uuid.UUID):
        with sessions() as db:
            r = db.get(ReviewCase, rid)
            if r is None:
                raise HTTPException(404, "Review not found")
            return {"id": r.id, "reason": r.reason, "status": r.status, "record": record_view(db.get(HospitalRecord, r.record_id)),
                    "assessments": [assessment_view(a) for a in db.scalars(select(Assessment).where(Assessment.version_id == r.version_id))]}

    static = Path(settings.static_dir)
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def dashboard():
        if not (static / "index.html").is_file():
            raise HTTPException(404, "Build the broker dashboard first")
        return FileResponse(static / "index.html")

    return app

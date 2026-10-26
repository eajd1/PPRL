"""Relational storage. Token payloads are never serialized by public API helpers."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (BigInteger, Boolean, Column, DateTime, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Hospital(Base):
    __tablename__ = "hospitals"
    id = Column(String(120), primary_key=True)
    credential_hash = Column(String(64), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)


class Submission(Base):
    __tablename__ = "submissions"
    sequence = Column(BigInteger, primary_key=True, autoincrement=True)
    id = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    site_id = Column(ForeignKey("hospitals.id"), nullable=False, index=True)
    idempotency_key = Column(String(120), nullable=False)
    digest = Column(String(64), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=now)
    completed_at = Column(DateTime(timezone=True))
    status = Column(String(30), nullable=False, default="queued", index=True)
    row_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    payload = Column(JSONB, nullable=False, default=list)
    results = Column(JSONB, nullable=False, default=list)
    error = Column(Text)
    __table_args__ = (UniqueConstraint("site_id", "idempotency_key"),)


class HospitalRecord(Base):
    __tablename__ = "hospital_records"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(ForeignKey("hospitals.id"), nullable=False)
    local_patient_id = Column(String(200), nullable=False)
    first_received_at = Column(DateTime(timezone=True), nullable=False)
    latest_received_at = Column(DateTime(timezone=True), nullable=False)
    content_changed_at = Column(DateTime(timezone=True), nullable=False)
    latest_version_id = Column(UUID(as_uuid=True), ForeignKey("record_versions.id", use_alter=True, name="fk_latest_version"))
    matching_version_id = Column(UUID(as_uuid=True), ForeignKey("record_versions.id", use_alter=True, name="fk_matching_version"))
    status = Column(String(40), nullable=False, default="pending")
    __table_args__ = (UniqueConstraint("site_id", "local_patient_id"),)


class RecordVersion(Base):
    __tablename__ = "record_versions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(ForeignKey("hospital_records.id"), nullable=False, index=True)
    submission_id = Column(ForeignKey("submissions.id"), nullable=False, index=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    token_version = Column(String(30), nullable=False)
    key_id = Column(String(120), nullable=False)
    method = Column(String(30), nullable=False)
    tokens = Column(JSONB, nullable=False)
    __table_args__ = (UniqueConstraint("record_id", "submission_id"),)


class RecordToken(Base):
    __tablename__ = "record_tokens"
    version_id = Column(ForeignKey("record_versions.id"), primary_key=True)
    position = Column(Integer, primary_key=True)
    value = Column(String(64), nullable=False)
    token_version = Column(String(30), nullable=False)
    key_id = Column(String(120), nullable=False)
    method = Column(String(30), nullable=False)
    __table_args__ = (Index("ix_token_lookup", "token_version", "key_id", "method", "position", "value"),)


class GlobalPatient(Base):
    __tablename__ = "global_patients"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    first_received_at = Column(DateTime(timezone=True), nullable=False)
    latest_received_at = Column(DateTime(timezone=True), nullable=False, index=True)


class Membership(Base):
    __tablename__ = "patient_memberships"
    record_id = Column(ForeignKey("hospital_records.id"), primary_key=True)
    patient_id = Column(ForeignKey("global_patients.id"), nullable=False, index=True)
    site_id = Column(ForeignKey("hospitals.id"), nullable=False)
    linked_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (UniqueConstraint("patient_id", "site_id"),)


class Assessment(Base):
    __tablename__ = "match_assessments"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    version_id = Column(ForeignKey("record_versions.id"), nullable=False, index=True)
    candidate_patient_id = Column(ForeignKey("global_patients.id"), nullable=False)
    candidate_record_id = Column(ForeignKey("hospital_records.id"), nullable=False)
    score = Column(Integer, nullable=False)
    evidence = Column(JSONB, nullable=False)
    scoring_version = Column(String(40), nullable=False)


class ReviewCase(Base):
    __tablename__ = "review_cases"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_id = Column(ForeignKey("hospital_records.id"), nullable=False, index=True)
    version_id = Column(ForeignKey("record_versions.id"), nullable=False)
    reason = Column(String(80), nullable=False)
    status = Column(String(20), nullable=False, default="open", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    submission_id = Column(ForeignKey("submissions.id"), nullable=False)
    record_id = Column(ForeignKey("hospital_records.id"))
    event = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    details = Column(JSONB, nullable=False, default=dict)

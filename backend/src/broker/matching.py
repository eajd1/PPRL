"""Transactional incremental matching. Called only under the worker advisory lock."""
from sqlalchemy import and_, or_, select, update
from .models import (Assessment, AuditEvent, GlobalPatient, HospitalRecord, Membership,
                     RecordToken, RecordVersion, ReviewCase)
from .scoring import SCORING_VERSION, WeightedScorer
from .validation import TOKEN_COLUMNS


def candidate_patients(db, record, version):
    conditions = [and_(RecordToken.position == i, RecordToken.value == value)
                  for i, value in enumerate(version.tokens, 1) if value]
    if not conditions:
        return set()
    query = (select(Membership.patient_id).join(HospitalRecord, Membership.record_id == HospitalRecord.id)
             .join(RecordToken, HospitalRecord.matching_version_id == RecordToken.version_id)
             .where(HospitalRecord.site_id != record.site_id,
                    RecordToken.token_version == version.token_version,
                    RecordToken.key_id == version.key_id, RecordToken.method == version.method,
                    or_(*conditions)).distinct())
    return set(db.scalars(query))


def members(db, patient_id):
    return list(db.scalars(select(HospitalRecord).join(Membership, Membership.record_id == HospitalRecord.id)
                           .where(Membership.patient_id == patient_id).order_by(HospitalRecord.site_id)))


def assess(db, record, version, patient_ids, scorer):
    groups = {}
    for patient_id in sorted(patient_ids, key=str):
        evidence = []
        same_site = False
        for peer in members(db, patient_id):
            if peer.id == record.id:
                continue
            same_site |= peer.site_id == record.site_id
            peer_version = db.get(RecordVersion, peer.matching_version_id) if peer.matching_version_id else None
            result = scorer.score(version, peer_version) if peer_version else {
                "score": 0, "matched_tokens": [], "group_scores": [0] * 6,
                "compatible": False, "classification": "below_threshold"}
            db.add(Assessment(version_id=version.id, candidate_patient_id=patient_id,
                              candidate_record_id=peer.id, score=result["score"], evidence=result,
                              scoring_version=SCORING_VERSION))
            evidence.append(result)
        groups[patient_id] = {"evidence": evidence, "same_site": same_site,
                              "relevant": any(e["score"] >= 6 for e in evidence),
                              "qualifies": bool(evidence) and all(e["classification"] == "match" for e in evidence)}
    return groups


def process_record(db, submission, row, scorer):
    record = db.scalar(select(HospitalRecord).where(HospitalRecord.site_id == submission.site_id,
                                                   HospitalRecord.local_patient_id == row["local_patient_id"]))
    received = submission.received_at
    # Submissions accepted before the 25-token upgrade may still be queued.
    # Only the five appended positions can be absent in that validated payload.
    tokens = [row[name] if i < 20 else row.get(name, "") for i, name in enumerate(TOKEN_COLUMNS)]
    if record is None:
        record = HospitalRecord(site_id=submission.site_id, local_patient_id=row["local_patient_id"],
                                first_received_at=received, latest_received_at=received, content_changed_at=received)
        db.add(record)
        db.flush()
    previous = db.get(RecordVersion, record.latest_version_id) if record.latest_version_id else None
    # Appending missing private-healthcare positions is not a content change.
    previous_tokens = previous.tokens + [""] * (len(TOKEN_COLUMNS) - len(previous.tokens)) if previous else None
    if previous is None or previous_tokens != tokens or any(getattr(previous, k) != row[k] for k in ("token_version", "key_id", "method")):
        record.content_changed_at = received
    record.latest_received_at = received
    version = RecordVersion(record_id=record.id, submission_id=submission.id, received_at=received,
                            tokens=tokens, **{k: row[k] for k in ("token_version", "key_id", "method")})
    db.add(version)
    db.flush()
    record.latest_version_id = version.id
    for i, value in enumerate(tokens, 1):
        if value:
            db.add(RecordToken(version_id=version.id, position=i, value=value,
                               token_version=version.token_version, key_id=version.key_id, method=version.method))
    membership = db.get(Membership, record.id)
    own_id = membership.patient_id if membership else None
    candidates = candidate_patients(db, record, version)
    if own_id:
        candidates.add(own_id)
    groups = assess(db, record, version, candidates, scorer)
    relevant = {pid: group for pid, group in groups.items() if group["relevant"]}
    reason = None
    patient_id = own_id
    if own_id:
        own = groups[own_id]
        if any(pid != own_id for pid in relevant):
            reason = "update_suggests_other_patient"
        elif own["evidence"] and not own["qualifies"]:
            reason = "update_lost_group_support"
    elif len(relevant) > 1:
        reason = "multiple_candidate_patients"
    elif relevant:
        candidate, group = next(iter(relevant.items()))
        if group["same_site"]:
            reason = "second_local_id_from_same_hospital"
        elif not group["qualifies"]:
            reason = "group_disagreement" if any(e["classification"] == "match" for e in group["evidence"]) else "review_range_evidence"
        else:
            patient_id = candidate
    else:
        patient = GlobalPatient(first_received_at=received, latest_received_at=received)
        db.add(patient)
        db.flush()
        patient_id = patient.id

    # Reassessment supersedes previous open cases; no operator decision is implied.
    db.execute(update(ReviewCase).where(ReviewCase.record_id == record.id, ReviewCase.status == "open").values(status="superseded"))
    if reason:
        record.status = "pending_review"
        db.add(ReviewCase(record_id=record.id, version_id=version.id, reason=reason))
    else:
        record.matching_version_id = version.id
        record.status = "linked" if any(tokens) else "insufficient_evidence"
        if membership is None:
            db.add(Membership(record_id=record.id, patient_id=patient_id, site_id=record.site_id))
    if patient_id:
        patient = db.get(GlobalPatient, patient_id)
        patient.latest_received_at = max(patient.latest_received_at, received)
    db.add(AuditEvent(submission_id=submission.id, record_id=record.id,
                      event="review_required" if reason else "record_linked" if not membership else "record_updated",
                      details={"global_patient_id": str(patient_id) if patient_id else None,
                               "reason": reason, "scoring_version": SCORING_VERSION}))
    db.flush()
    return {"local_patient_id": record.local_patient_id,
            "global_patient_id": str(patient_id) if patient_id else None,
            "status": record.status, "latest_received_at": received.isoformat(),
            "content_changed_at": record.content_changed_at.isoformat()}


def process_submission(db, submission, scorer=None):
    scorer = scorer or WeightedScorer()
    submission.results = [process_record(db, submission, row, scorer)
                          for row in sorted(submission.payload, key=lambda r: r["local_patient_id"])]
    submission.status = "processed"
    from .models import now
    submission.completed_at = now()
    # Immutable versions retain the token history; drop duplicate staging payload.
    submission.payload = []

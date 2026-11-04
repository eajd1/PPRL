import logging
from sqlalchemy import select, text
from .matching import process_submission
from .models import Submission

LOCK_ID = 72423019
log = logging.getLogger(__name__)


def process_next(factory):
    """A crash rolls back the entire submission, leaving it queued for recovery."""
    with factory.begin() as db:
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_ID}):
            return False
        submission = db.scalar(select(Submission).where(Submission.status == "queued")
                               .order_by(Submission.sequence).limit(1).with_for_update())
        if submission is None:
            return False
        # A savepoint permits a safe failure status without exposing partial links.
        try:
            with db.begin_nested():
                process_submission(db, submission)
        except Exception:
            # Never log SQL parameters, token payloads or exception representations.
            log.error("Broker matching failed; submission %s rolled back", submission.id)
            submission.status = "failed"
            submission.error = "Matching failed; retry this submission after checking broker configuration."
        return True


def run_worker(factory, stop):
    while not stop.is_set():
        try:
            busy = process_next(factory)
        except Exception:
            log.error("Broker database temporarily unavailable")
            busy = False
        if not busy:
            stop.wait(1)

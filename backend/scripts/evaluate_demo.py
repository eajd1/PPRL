"""Evaluate existing broker linkage using a verified synthetic ground-truth mapping.

Example: BROKER_EVAL_DATABASE_URL=postgresql+psycopg://.../pprl
python backend/scripts/evaluate_demo.py
The broker database is queried read-only. No raw identifiers or credentials are
included in the aggregate report.
"""
import csv
import hashlib
import itertools
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
from sqlalchemy import create_engine, select, tuple_
from sqlalchemy.engine import make_url
from src.broker.models import HospitalRecord, Membership
from pair_results import pair_rows, write_pair_results


def log_progress(message):
    print(f"[evaluate_demo] {message}", file=sys.stderr, flush=True)


def hospital_id_from_filename(path):
    match = re.search(r"(\d+)", path.stem)
    if not match:
        raise ValueError(f"No hospital number in dataset filename {path.name}")
    return match.group(1)


def normalize_header(value):
    name = re.sub(r"[\s-]+", "_", value.strip().lower())
    return {"patient_id": "local_patient_id", "site_id": "hospital_id"}.get(name, name)


def verified_truth(dataset):
    with (dataset / "ground_truth.csv").open(newline="") as source:
        truth_rows = list(csv.reader(source))
    files = sorted(dataset.glob("hospital*.csv"), key=lambda p: int(hospital_id_from_filename(p)))
    if len(files) != len(truth_rows):
        raise ValueError("Ground-truth hospital count does not match the datasets")
    result = {}
    population = max(int(v) for row in truth_rows for v in row) + 1
    for path, labels in zip(files, truth_rows):
        with path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                raise ValueError(f"Dataset {path.name} has no header")
            columns = {}
            for header in reader.fieldnames:
                name = normalize_header(header)
                if name in columns:
                    raise ValueError(f"Dataset {path.name} has duplicate normalized headers")
                columns[name] = header
            if "local_patient_id" not in columns:
                raise ValueError(f"Dataset {path.name} is missing a patient ID column")
            rows = list(reader)
        if len(rows) != len(labels):
            raise ValueError("Ground-truth row count does not match the hospital dataset")
        local_to_truth, truth_to_local, offsets = {}, {}, set()
        filename_site = hospital_id_from_filename(path)
        for record, truth in zip(rows, labels):
            local = (record.get(columns["local_patient_id"]) or "").strip()
            site = ((record.get(columns["hospital_id"]) or "").strip()
                    if "hospital_id" in columns else filename_site)
            if not local or not site:
                raise ValueError("Dataset contains a blank hospital or local patient ID")
            if local in local_to_truth and local_to_truth[local] != truth:
                raise ValueError("Conflicting truth for repeated local patient ID")
            if truth in truth_to_local and truth_to_local[truth] != local:
                raise ValueError("Multiple local IDs for one truth identity in a hospital")
            local_to_truth[local], truth_to_local[truth] = truth, local
            offsets.add((int(local) - int(truth)) % population)
            result[(site, local)] = truth
        if len(offsets) != 1:
            raise ValueError("Positional truth does not satisfy the supplied generator's hospital-ID mapping")
    return files, result


def load_existing_predictions(url, truth):
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                rows = connection.execute(
                    select(HospitalRecord.site_id, HospitalRecord.local_patient_id,
                           Membership.patient_id, HospitalRecord.status)
                    .outerjoin(Membership, Membership.record_id == HospitalRecord.id)
                    .where(tuple_(HospitalRecord.site_id, HospitalRecord.local_patient_id).in_(list(truth)))
                )
                existing = {
                    (row.site_id, row.local_patient_id): (
                        str(row.patient_id) if row.patient_id is not None else None,
                        row.status,
                    )
                    for row in rows
                }
    finally:
        engine.dispose()

    if not existing:
        raise SystemExit("No dataset records found in the selected broker database")
    predictions = {
        identity: existing[identity][0] if identity in existing else None
        for identity in truth
    }
    missing_records = len(truth) - len(existing)
    unlinked_records = sum(patient_id is None for patient_id, _ in existing.values())
    reviews = sum(status == "pending_review" for _, status in existing.values())
    return predictions, len(existing), missing_records, unlinked_records, reviews, {identity: status for identity, (_, status) in existing.items()}


def main():
    root = PROJECT_ROOT
    url = os.environ.get("BROKER_EVAL_DATABASE_URL", "")
    log_progress("Checking existing broker database configuration")
    if not url:
        raise SystemExit("Set BROKER_EVAL_DATABASE_URL to the existing broker database")
    parsed_url = make_url(url)
    if parsed_url.drivername != "postgresql+psycopg" or not parsed_url.database:
        raise SystemExit("BROKER_EVAL_DATABASE_URL must be a PostgreSQL psycopg URL with a database name")
    log_progress("Verifying dataset and ground truth")
    files, truth = verified_truth(root / "backend/dataset")
    log_progress(f"Verified {len(files)} hospital datasets and {len(truth)} records")
    output = root / "backend/runtime/evaluation"
    log_progress("Reading existing broker matches in a read-only transaction")
    predictions, database_records, missing_records, unlinked_records, reviews, statuses = load_existing_predictions(url, truth)
    log_progress(
        f"Found {database_records}/{len(truth)} dataset records; "
        f"{missing_records} missing, {unlinked_records} unlinked, {reviews} pending review"
    )
    log_progress(f"Scoring {len(predictions)} expected records")
    tp = fp = fn = tn = 0
    for a, b in itertools.combinations(predictions, 2):
        if a[0] == b[0]:
            continue
        actual = truth[a] == truth[b]
        predicted = predictions[a] is not None and predictions[a] == predictions[b]
        tp += actual and predicted
        fp += not actual and predicted
        fn += actual and not predicted
        tn += not actual and not predicted
    report = {"scoring_version": "existing-memberships-see-version-history", "threshold_match": 12, "threshold_review": 6,
              "records": len(predictions), "database_records": database_records,
              "missing_records": missing_records, "unlinked_records": unlinked_records,
              "pending_review": reviews, "review_rate": reviews / len(predictions),
              "true_positives": tp, "false_positives": fp, "false_negatives": fn, "true_negatives": tn,
              "pairwise_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
              "pairwise_precision": tp / (tp + fp) if tp + fp else None,
              "pairwise_recall": tp / (tp + fn) if tp + fn else None,
              "truth_checks": ["row alignment", "consistent local-to-truth mappings", "one local ID per truth per hospital", "generator modulo mapping"],
              "dataset_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [*files, root / "backend/dataset/ground_truth.csv"]},
              "note": "Read-only evaluation of existing broker memberships against synthetic truth. Missing records and records without a membership count as unlinked; pending review is reported separately. Do not interpret scores as probabilities."}
    output.mkdir(parents=True, exist_ok=True)
    pair_path = output / "evaluation_pairs.csv"
    with pair_path.open("w",newline="",encoding="utf-8") as handle:
        report["pair_results_rows"] = write_pair_results(handle,pair_rows(
            predictions,truth,scheme="existing-memberships",cohort="verified_repository_dataset",statuses=statuses))
    report["pair_results_csv"] = str(pair_path)
    report_path = output / "evaluation.json"
    log_progress(f"Writing report to {report_path}")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    log_progress("Evaluation complete")


if __name__ == "__main__":
    main()

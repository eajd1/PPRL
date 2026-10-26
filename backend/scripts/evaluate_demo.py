"""Evaluate synthetic linkage using a verified mapping in an EMPTY pprl_eval* database.

Example: BROKER_EVAL_DATABASE_URL=postgresql+psycopg://.../pprl_eval
python backend/scripts/evaluate_demo.py
No raw identifiers or secret credentials are included in the aggregate report.
"""
import csv
import hashlib
import itertools
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from fastapi.testclient import TestClient
from alembic import command
from alembic.config import Config
from src.broker.api import create_app
from src.broker.config import Settings, credential_hash
from src.broker.models import Submission
from src.broker.worker import process_next
from src.hospital import cleaning, pipeline, token_generator


def verified_truth(dataset):
    with (dataset / "ground_truth.csv").open(newline="") as source:
        truth_rows = list(csv.reader(source))
    files = sorted(dataset.glob("hospital*.csv"), key=lambda p: int(cleaning.hospital_id_from_filename(p)))
    if len(files) != len(truth_rows):
        raise ValueError("Ground-truth hospital count does not match the datasets")
    result = {}
    population = max(int(v) for row in truth_rows for v in row) + 1
    for path, labels in zip(files, truth_rows):
        frame, _ = cleaning.read_hospital_csv(path)
        if len(frame) != len(labels):
            raise ValueError("Ground-truth row count does not match the hospital dataset")
        local_to_truth, truth_to_local, offsets = {}, {}, set()
        for record, truth in zip(frame.to_dict("records"), labels):
            local = record["local_patient_id"].strip()
            if local in local_to_truth and local_to_truth[local] != truth:
                raise ValueError("Conflicting truth for repeated local patient ID")
            if truth in truth_to_local and truth_to_local[truth] != local:
                raise ValueError("Multiple local IDs for one truth identity in a hospital")
            local_to_truth[local], truth_to_local[truth] = truth, local
            offsets.add((int(local) - int(truth)) % population)
            result[(record["hospital_id"], local)] = truth
        if len(offsets) != 1:
            raise ValueError("Positional truth does not satisfy the supplied generator's hospital-ID mapping")
    return files, result


def main():
    root = Path(__file__).resolve().parents[2]
    url = os.environ.get("BROKER_EVAL_DATABASE_URL", "")
    if not url or not make_url(url).database.startswith("pprl_eval"):
        raise SystemExit("Set BROKER_EVAL_DATABASE_URL to a dedicated empty pprl_eval* database")
    files, truth = verified_truth(root / "backend/dataset")
    os.environ["BROKER_DATABASE_URL"] = url
    command.upgrade(Config(str(root / "backend/alembic.ini")), "head")
    settings = Settings(database_url=url, admin_hash=credential_hash("evaluation-admin"),
                        hospitals={str(i): credential_hash(f"evaluation-{i}") for i in range(1, 4)},
                        key_ids=(token_generator.TOKEN_KEY_ID,))
    output = root / "backend/runtime/evaluation"
    key = pipeline.validate_configuration()
    predictions, reviews = {}, 0
    with TestClient(create_app(settings, start_worker=False)) as client:
        with client.app.state.sessions() as db:
            if db.scalar(select(func.count()).select_from(Submission)):
                raise SystemExit("Evaluation database must be empty; use a new database")
        for path in files:
            report = pipeline.process_file(path, output, overwrite=True, key=key)
            site = str(report["hospital_id"])
            data = Path(report["token_file"]).read_bytes()
            response = client.post("/api/v1/submissions", headers={"Authorization": f"Bearer evaluation-{site}", "Idempotency-Key": f"evaluation-{site}"}, files={"file": ("tokens.csv", data, "text/csv")})
            response.raise_for_status()
            sid = response.json()["id"]
            process_next(client.app.state.sessions)
            offset = 0
            while True:
                response = client.get(f"/api/v1/submissions/{sid}/results?offset={offset}&limit=100", headers={"Authorization": f"Bearer evaluation-{site}"})
                response.raise_for_status()
                page = response.json()
                if page["status"] != "processed":
                    raise RuntimeError("Evaluation matching did not complete")
                for r in page["items"]:
                    identity = (site, r["local_patient_id"])
                    if identity not in truth:
                        raise ValueError("Token identity is absent from verified truth")
                    predictions[identity] = r["global_patient_id"]
                    reviews += r["status"] == "pending_review"
                offset += len(page["items"])
                if offset >= page["total"]:
                    break
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
    report = {"scoring_version": "grouped-24-v1", "threshold_match": 12, "threshold_review": 6,
              "records": len(predictions), "pending_review": reviews, "review_rate": reviews / len(predictions),
              "true_positives": tp, "false_positives": fp, "false_negatives": fn, "true_negatives": tn,
              "pairwise_precision": tp / (tp + fp) if tp + fp else None,
              "pairwise_recall": tp / (tp + fn) if tp + fn else None,
              "truth_checks": ["row alignment", "consistent local-to-truth mappings", "one local ID per truth per hospital", "generator modulo mapping"],
              "dataset_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [*files, root / "backend/dataset/ground_truth.csv"]},
              "note": "Synthetic evaluation only; pending records count as unlinked. Do not interpret scores as probabilities."}
    output.mkdir(parents=True, exist_ok=True)
    (output / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

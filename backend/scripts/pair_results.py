"""Stream cross-hospital pair classifications without patient attributes or tokens."""
import csv
import itertools

PAIR_COLUMNS = (
    "scheme", "cohort", "hospital_id_a", "local_patient_id_a",
    "hospital_id_b", "local_patient_id_b", "ground_truth_id_a", "ground_truth_id_b",
    "ground_truth_match", "predicted_match", "outcome", "global_patient_id_a",
    "global_patient_id_b", "status_a", "status_b",
)


def pair_rows(predictions, truth, *, scheme="", cohort="", statuses=None):
    """Every cross-hospital pair, including true negatives; local IDs stay strings.

    predicted_match means membership in the same global patient, not merely a
    candidate score or review suggestion. Missing and unassigned records do not match.
    """
    for a, b in itertools.combinations(sorted(truth), 2):
        if a[0] == b[0]:
            continue
        left, right = predictions.get(a), predictions.get(b)
        actual = truth[a] == truth[b]
        predicted = left is not None and right is not None and left == right
        outcome = ("true_positive" if actual else "false_positive") if predicted else (
            "false_negative" if actual else "true_negative")
        def status(identity, global_id):
            if statuses is not None:
                return statuses.get(identity, "missing")
            return "missing" if identity not in predictions else "linked" if global_id is not None else "unassigned"
        yield dict(scheme=scheme, cohort=cohort, hospital_id_a=a[0], local_patient_id_a=a[1],
                   hospital_id_b=b[0], local_patient_id_b=b[1], ground_truth_id_a=truth[a],
                   ground_truth_id_b=truth[b], ground_truth_match=actual, predicted_match=predicted,
                   outcome=outcome, global_patient_id_a=left or "", global_patient_id_b=right or "",
                   status_a=status(a, left), status_b=status(b, right))


def write_pair_results(handle, rows, *, include_header=True):
    """Write one row at a time so the quadratic pair set is never kept in memory."""
    writer = csv.DictWriter(handle, fieldnames=PAIR_COLUMNS, lineterminator="\n")
    if include_header:
        writer.writeheader()
    count = 0
    for row in rows:
        writer.writerow(row)
        count += 1
    return count

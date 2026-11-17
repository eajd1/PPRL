"""Pair exports must agree with aggregate metrics and preserve local ID strings."""
import csv
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from pair_results import pair_rows, write_pair_results


def fixture():
    truth={("1","001"):"A",("1","002"):"B",("2","003"):"A",("2","004"):"C",("3","005"):"B"}
    predictions={("1","001"):"g1",("1","002"):"g2",("2","003"):"g1",("2","004"):"g2",("3","005"):None}
    return truth,predictions


def test_pairs_include_matches_misses_and_incorrect_links():
    truth,predictions=fixture()
    rows=list(pair_rows(predictions,truth,scheme="v4",cohort="test",statuses={("3","005"):"pending_review"}))
    assert len(rows)==8
    assert all(r["hospital_id_a"]!=r["hospital_id_b"] for r in rows)
    counts={outcome:sum(r["outcome"]==outcome for r in rows) for outcome in ("true_positive","false_positive","false_negative","true_negative")}
    assert counts==dict(true_positive=1,false_positive=1,false_negative=1,true_negative=5)
    matched=next(r for r in rows if r["outcome"]=="true_positive")
    assert matched["local_patient_id_a"]=="001" and matched["local_patient_id_b"]=="003"
    missed=next(r for r in rows if r["outcome"]=="false_negative")
    assert missed["status_b"]=="pending_review" and missed["global_patient_id_b"]==""
    assert missed["ground_truth_match"] and not missed["predicted_match"]
    assert missed["scheme"]=="v4" and missed["cohort"]=="test"


def test_unassigned_or_missing_records_never_count_as_a_match():
    truth={("1","0001"):"same",("2","0002"):"same"}
    rows=list(pair_rows({("1","0001"):None},truth))
    assert rows[0]["outcome"]=="false_negative"
    assert not rows[0]["predicted_match"]
    assert rows[0]["status_b"]=="missing"


def test_csv_roundtrip_and_metrics_agree(tmp_path):
    from evaluate_token_schemes import metrics
    truth,predictions=fixture()
    path=tmp_path/"pairs.csv"
    with path.open("w",newline="",encoding="utf-8") as out:
        count=write_pair_results(out,pair_rows(predictions,truth,scheme="v4",cohort="comma, unicode \u00e9"))
    with path.open(newline="",encoding="utf-8") as source:rows=list(csv.DictReader(source))
    assert count==len(rows)==8
    assert rows[0]["cohort"]=="comma, unicode \u00e9"
    summary=metrics(predictions,truth,1)
    for outcome,metric in [("true_positive","true_positives"),("false_positive","false_positives"),("false_negative","false_negatives")]:
        assert sum(r["outcome"]==outcome for r in rows)==summary[metric]
    assert rows[0]["local_patient_id_a"]=="001"
    assert not any("token" in name or "key" in name for name in rows[0])

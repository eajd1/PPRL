import csv
import hashlib
import hmac
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.hospital.combining import build_combination_strings, COMBINATIONS
from src.broker.scoring import WeightedScorer

FIELDS = dict(first_name="alice", last_name="smith", date_of_birth="1990-02-03",
              postcode="2000", phone="+61412345678", email="alice@example.test",
              fake_medicare_id="1234567890", private_healthcare="0987654321")
EXPECTED = ["1234567890|1990-02-03", "0987654321|1990-02-03",
            "1234567890|smith|a", "0987654321|smith|a", "alice|smith|1990-02-03",
            "a|smith|1990-02-03|2000", "alice|s|1990-02-03|2000",
            "smith|1990-02-03|2000", "alice|1990-02-03|2000", "+61412345678|1990-02-03",
            "alice@example.test|1990-02-03", "alice|smith|+61412345678",
            "alice|smith|alice@example.test", "alice|smith|2000"]

def version(positions, scheme="v4"):
    return SimpleNamespace(token_version=scheme, key_id="test-key", method="hmac_sha256",
        tokens=[hashlib.sha256(str(i).encode()).hexdigest() if i in positions else "" for i in range(1,15)])

def test_document_combinations_and_missing_fields():
    assert list(build_combination_strings(FIELDS).values()) == EXPECTED
    assert len(COMBINATIONS) == 14
    assert list(build_combination_strings({**FIELDS, "middle_name":"", "sex":"U"}).values()) == EXPECTED
    for field in FIELDS:
        actual = build_combination_strings({**FIELDS, field:""})
        for name, fields in COMBINATIONS:
            if field in fields or (field == "first_name" and "first_initial" in fields) or (field == "last_name" and "last_initial" in fields):
                assert actual[name] == ""
    assert not any(build_combination_strings({}).values())

@pytest.mark.parametrize("positions,groups,total,classification", [
    ([14],[0,0,0,0,1],1,"below_threshold"),
    ([1,3],[8,0,0,0,0],8,"review"),
    ([10,11],[0,0,5,5,0],10,"review"),
    ([1,10],[8,0,5,0,0],13,"match"),
    ([5,10,11,12,13],[0,5,5,5,0],15,"match"),
    (list(range(1,15)),[8,5,5,5,1],24,"match"),
    ([3],[6,0,0,0,0],6,"review"),
    ([3,10],[6,0,5,0,0],11,"review"),
    ([3,10,14],[6,0,5,0,1],12,"match"),
    ([],[0,0,0,0,0],0,"below_threshold"),
])
def test_document_scoring_examples(positions,groups,total,classification):
    result=WeightedScorer().score(version(range(1,15)),version(positions))
    assert result["group_scores"] == groups
    assert (result["score"],result["classification"]) == (total,classification)

def test_v3_tokens_never_compare_as_v4():
    assert not WeightedScorer().score(version([1]),version([1],"v3"))["compatible"]


def test_export_refuses_legacy_combination_headers(tmp_path):
    from src.hospital import token_generator as tg
    path=tmp_path/"hospital1_combinations.csv"
    path.write_text("hospital_id,local_patient_id,old_combination\n1,001,synthetic\n")
    with pytest.raises(tg.TokenisationError):
        tg.tokenise_file(path,tmp_path/"out",tmp_path/"reports","hmac_sha256",b"x"*32,"test-key")


def test_pipeline_accepts_csv_without_middle_name_or_sex(tmp_path):
    from src.hospital.pipeline import process_file
    source=tmp_path/"hospital1.csv"
    values={**FIELDS,"hospital_id":"1","local_patient_id":"000123"}
    with source.open("w",newline="") as out:
        writer=csv.DictWriter(out,fieldnames=list(values));writer.writeheader();writer.writerow(values)
    report=process_file(source,tmp_path/"runtime",key=b"x"*32)
    with Path(report["token_file"]).open(newline="") as out:
        record=next(csv.DictReader(out))
    assert record["token_version"]=="v4" and record["local_patient_id"]=="000123"
    assert len(record)==19 and all(record[f"token{i}"] for i in range(1,15))

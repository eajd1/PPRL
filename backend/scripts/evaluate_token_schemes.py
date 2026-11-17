"""Compare frozen v3 and v4 in two EMPTY pprl_eval* databases; no production data is changed.

Requires --v3-url, --v4-url and --output. The baseline stages frozen v3 payloads
at the durable queue boundary; v4 exercises current CSV validation/API and the
same matching worker. HTTPS transport is verified separately by live TLS tests.
"""
import argparse
import csv
import hashlib
import hmac
import io
import itertools
import json
import os
from pathlib import Path
import random
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "backend"))
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.engine import make_url
from alembic import command
from alembic.config import Config
from src.broker.api import create_app
from src.broker.config import Settings, credential_hash
from src.broker.models import Submission
from src.broker.worker import process_next
from src.hospital import pipeline, combining, token_generator
from evaluate_demo import verified_truth
from legacy_v3 import COMBINATIONS as OLD_COMBINATIONS

KEY=b"evaluation-only-key-not-for-real-data" * 2


def encode_rows(cleaned, scheme):
    result=[]
    for source in cleaned:
        if scheme=="v4":
            combinations=combining.build_combination_strings(source)
        else:
            values={k:(v or "").strip() for k,v in source.items()}
            values.update(first_initial=values.get("first_name","")[:1],
                          last_initial=values.get("last_name","")[:1],
                          middle_initial=values.get("middle_name","")[:1],
                          dob_dmy=combining._format_date(values.get("date_of_birth",""),"%d/%m/%Y"),
                          dob_mdy=combining._format_date(values.get("date_of_birth",""),"%m/%d/%Y"))
            if values.get("sex")=="U": values["sex"]=""
            combinations={name:"|".join(values.get(k,"") for k in fields) if all(values.get(k,"") for k in fields) else ""
                          for name,fields in OLD_COMBINATIONS}
        tokens={f"token{i}":hmac.new(KEY,"\x1f".join((scheme,name,value)).encode(),hashlib.sha256).hexdigest() if value else ""
                for i,(name,value) in enumerate(combinations.items(),1)}
        result.append(dict(site_id=source["hospital_id"],local_patient_id=source["local_patient_id"],
                           token_version=scheme,key_id="evaluation-only",method="hmac_sha256",**tokens))
    return result


def synthetic(seed):
    rng=random.Random(seed)
    data={str(s):[] for s in (1,2,3)}; truth={}
    for person in range(40):
        base=dict(first_name=f"name{chr(97+person//26)}{chr(97+person%26)}",last_name=f"family{chr(97+person//2)}",middle_name=f"middle{chr(97+person//26)}{chr(97+person%26)}",
                  date_of_birth=f"1980-01-{person//2%28+1:02d}",phone=f"+614{person//2:08d}",
                  email=f"person{person}@example.test",postcode="2000",sex="F",
                  fake_medicare_id=f"{1000000000+person//2}",private_healthcare=f"{2000000000+person//2}")
        for site in data:
            row=base.copy()
            if site!="1":
                for field in ("middle_name","phone","email","postcode"):
                    if rng.random()<0.25: row[field]=""
                if rng.random()<0.15:row["first_name"]+="typo"
                if rng.random()<0.12:row["date_of_birth"]=""
            row.update(hospital_id=site,local_patient_id=f"seed{seed}-site{site}-local{rng.randrange(1000000)}-{person}")
            data[site].append(row);truth[(site,row["local_patient_id"])]=str(person)
    return data,truth


def metrics(predictions,truth,reviews):
    tp=fp=fn=0
    for a,b in itertools.combinations(truth,2):
        if a[0]==b[0]:continue
        actual=truth[a]==truth[b]
        predicted=predictions[a] is not None and predictions[a]==predictions[b]
        tp+=actual and predicted; fp+=(not actual) and predicted; fn+=actual and not predicted
    return dict(records=len(truth),true_positives=tp,false_positives=fp,false_negatives=fn,
                precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,
                f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,review_rate=reviews/len(truth))


def evaluate(url,scheme,cohorts):
    parsed=make_url(url)
    if parsed.drivername!="postgresql+psycopg" or not parsed.database.startswith("pprl_eval"):
        raise ValueError("Evaluation requires dedicated PostgreSQL pprl_eval* databases")
    os.environ["BROKER_DATABASE_URL"]=url
    command.upgrade(Config(str(ROOT/"backend/alembic.ini")),"head")
    settings=Settings(database_url=url,admin_hash=credential_hash("evaluation-admin"),
                      hospitals={s:credential_hash("evaluation-"+s) for s in ("1","2","3")},key_ids=tuple("evaluation-"+name for name in cohorts))
    reports={}
    with TestClient(create_app(settings,start_worker=False)) as client:
        with client.app.state.sessions() as db:
            if db.scalar(select(func.count()).select_from(Submission)):
                raise ValueError("Evaluation database is not empty; refusing to reuse or clear it")
        for name,(data,truth) in cohorts.items():
            predictions={};reviews=0
            for site,cleaned in data.items():
                rows=encode_rows(cleaned,scheme)
                # Isolate cohorts so repeated synthetic identifiers cannot cross-link across seeds.
                for row in rows: row["key_id"]="evaluation-"+name
                if scheme=="v3":
                    with client.app.state.sessions.begin() as db:
                        sub=Submission(site_id=site,idempotency_key=name,digest=hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
                                       payload=rows,row_count=len(rows));db.add(sub);db.flush();sid=str(sub.id)
                else:
                    output=io.StringIO(newline="");writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
                    response=client.post("/api/v1/submissions",headers={"Authorization":"Bearer evaluation-"+site,"Idempotency-Key":name},
                                         files={"file":("tokens.csv",output.getvalue().encode(),"text/csv")})
                    response.raise_for_status();sid=response.json()["id"]
                assert process_next(client.app.state.sessions)
                offset=0
                while True:
                    response=client.get(f"/api/v1/submissions/{sid}/results?offset={offset}&limit=100",headers={"Authorization":"Bearer evaluation-"+site})
                    response.raise_for_status();body=response.json()
                    if body["status"]!="processed":raise RuntimeError("Evaluation submission did not process")
                    for item in body["items"]:
                        predictions[(site,item["local_patient_id"])]=item["global_patient_id"]
                        reviews+=item["status"]=="pending_review"
                    offset+=len(body["items"])
                    if offset>=body["total"]:break
            assert set(predictions)==set(truth)
            reports[name]=metrics(predictions,truth,reviews)
    return reports


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v3-url",required=True);parser.add_argument("--v4-url",required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.v3_url==args.v4_url:raise ValueError("Use separate databases for each scheme")
    files,truth=verified_truth(ROOT/"backend/dataset")
    cohorts={}
    with tempfile.TemporaryDirectory() as tmp:
        data={}
        for path in files:
            report=pipeline.process_file(path,Path(tmp),overwrite=True,key=KEY)
            clean=Path(tmp)/"linkage_patients"/f"{path.stem}_linkage_patients.csv"
            with clean.open(newline="",encoding="utf-8-sig") as source:data[str(report["hospital_id"])]=list(csv.DictReader(source))
        cohorts["verified_repository_dataset"]=(data,truth)
    for seed in (1729,2718,31415):cohorts[f"held_out_seed_{seed}_shared_family_ids"]=synthetic(seed)
    results={scheme:evaluate(url,scheme,cohorts) for scheme,url in (("v3",args.v3_url),("v4",args.v4_url))}
    report=dict(thresholds={"match":12,"review":6},results=results,
                note="Provisional fixed weights; no tuning on these seeds. Shared family IDs, DOB and phone are deliberate stress cases. No clinical accuracy claim.",
                dataset_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [*files,ROOT/"backend/dataset/ground_truth.csv"]})
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__":main()

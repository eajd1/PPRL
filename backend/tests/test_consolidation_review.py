import csv
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.hospital import cleaning, combining, pipeline


def visits(conflict=True):
    base = dict(hospital_id='2', local_patient_id='038', first_name='Ram',
                last_name='Pham', middle_name='Gard', email='patient@example.test',
                date_of_birth='2011-03-09', sex='M', postcode='2363',
                phone='0477855754', fake_medicare_id='1537425047',
                private_healthcare='8101332077', visit_date='2026-01-01')
    return [{**base, 'first_name': first, 'middle_name': middle,
             'visit_date': f'2026-01-0{i}'}
            for i, (first, middle) in enumerate(
                [('Raymon','Gard'), ('Raymon','Gardner'), ('Ram','Gard'), ('Ram','Gardner')]
                if conflict else [('Ram','Gard')], 1)]


def csv_data(rows):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def source_file(tmp_path, conflict=True):
    path = tmp_path / 'hospital2.csv'
    path.write_bytes(csv_data(visits(conflict)))
    return path


def test_conflicts_include_ties_majorities_and_counts():
    rows = visits()
    rows[0]['last_name'] = 'Other'
    frame = pd.DataFrame(rows)
    patients = cleaning.collect_patient_conflicts(frame)
    fields = {item['field']: item for item in patients[0]['fields']}
    assert fields['first_name']['suggested_value'] is None
    assert fields['last_name']['suggested_value'] == 'Pham'
    assert fields['last_name']['resolution'] == 'majority'
    assert fields['middle_name']['values'] == [
        {'value': 'Gard', 'count': 2}, {'value': 'Gardner', 'count': 2}]
    assert patients[0]['local_patient_id'] == '038'


def test_one_nonblank_value_is_not_a_conflict():
    rows = visits(False) * 2
    rows[1] = {**rows[1], 'first_name': ''}
    assert cleaning.collect_patient_conflicts(pd.DataFrame(rows)) == []


def test_unique_maximum_completes_without_review(tmp_path):
    rows = visits()
    for row in rows[:3]:
        row.update(first_name='Ram', middle_name='Gard')
    rows[3].update(first_name='Raymon', middle_name='Gardner')
    source = tmp_path/'hospital2.csv'
    source.write_bytes(csv_data(rows))
    summary = pipeline.process_file(source, tmp_path/'output', key=b'x'*32)
    assert summary['status'] == 'success' and summary['conflict_fields'] == 0
    assert json.loads(Path(summary['conflicts_file']).read_text())['patients'] == []
    with Path(summary['linkage_patient_file']).open(newline='') as handle:
        patient = next(csv.DictReader(handle))
    assert patient['first_name'] == 'ram' and patient['middle_name'] == 'gard'
    report = json.loads(Path(summary['report_file']).read_text())['patient_consolidation']
    assert report['fields_resolved_using_unique_majority']['first_name'] == 1
    assert report['patients_with_unresolved_ties'] == 0


def test_mixed_winner_and_tie_only_reviews_the_tied_field(tmp_path):
    rows = visits()
    for row in rows[:3]: row['first_name'] = 'Ram'
    rows[3]['first_name'] = 'Raymon'
    source = tmp_path/'hospital2.csv'
    source.write_bytes(csv_data(rows))
    prepared = pipeline.process_file(source, tmp_path/'output', key=b'x'*32)
    assert prepared['status'] == 'awaiting_review' and prepared['conflict_fields'] == 1
    fields = json.loads(Path(prepared['conflicts_file']).read_text())['patients'][0]['fields']
    assert [field['field'] for field in fields] == ['middle_name']
    resolved = pipeline.finalize_file(prepared, tmp_path/'resolved', key=b'x'*32,
                                     resolutions={'038': {'middle_name': 'gardner'}})
    with Path(resolved['linkage_patient_file']).open(newline='') as handle:
        patient = next(csv.DictReader(handle))
    assert patient['first_name'] == 'ram' and patient['middle_name'] == 'gardner'
    report = json.loads(Path(resolved['report_file']).read_text())['patient_consolidation']
    assert report['fields_resolved_using_unique_majority']['first_name'] == 1
    assert report['fields_confirmed_locally']['middle_name'] == 1
    assert report['patients_with_unresolved_ties'] == 0


def test_unique_maximum_does_not_require_more_than_half_the_rows():
    rows = visits(False) * 4
    rows = [{**row, 'first_name': name} for row, name in zip(rows, ['Ram','Ram','Raymon','Ramon'])]
    assert cleaning.collect_patient_conflicts(pd.DataFrame(rows), ties_only=True) == []
    table, report = cleaning.build_patient_linkage_table(pd.DataFrame(rows))
    assert table.iloc[0]['first_name'] == 'Ram'
    assert report['fields_resolved_using_unique_majority']['first_name'] == 1


def test_top_tie_still_requires_review_even_with_lower_counts():
    rows = visits(False) * 5
    rows = [{**row, 'first_name': name} for row, name in zip(rows, ['Ram','Ram','Raymon','Raymon','Ramon'])]
    fields = cleaning.collect_patient_conflicts(pd.DataFrame(rows), ties_only=True)[0]['fields']
    assert fields[0]['resolution'] == 'tie' and fields[0]['suggested_value'] is None


def test_old_review_jobs_can_still_confirm_majority_fields(tmp_path):
    rows = visits()
    rows[0]['last_name'] = 'Other'
    source = tmp_path/'hospital2.csv'
    source.write_bytes(csv_data(rows))
    prepared = pipeline.process_file(source, tmp_path/'output', key=b'x'*32)
    # Existing jobs have no policy marker and stored all conflicts, not just ties.
    prepared.pop('consolidation_review_policy')
    cleaned = pd.read_csv(prepared['cleaned_file'], dtype=str, keep_default_na=False)
    Path(prepared['conflicts_file']).write_text(json.dumps({
        'hospital_id':'2', 'patients':cleaning.collect_patient_conflicts(cleaned)}))
    resolved = pipeline.finalize_file(prepared, tmp_path/'resolved', key=b'x'*32,
        resolutions={'038': {'first_name':'ram', 'middle_name':'gard', 'last_name':'other'}})
    with Path(resolved['linkage_patient_file']).open(newline='') as handle:
        assert next(csv.DictReader(handle))['last_name'] == 'other'


def test_pipeline_pauses_without_tokens_and_preserves_all_visits(tmp_path):
    summary = pipeline.process_file(source_file(tmp_path), tmp_path/'output', key=b'x'*32)
    assert summary['status'] == 'awaiting_review'
    assert summary['conflict_patients'] == 1
    assert 'token_file' not in summary
    assert not (tmp_path/'output'/'tokens').exists()
    with Path(summary['cleaned_file']).open(newline='') as source:
        assert len(list(csv.DictReader(source))) == 4


def test_resolution_selects_observed_names_or_explicit_blank(tmp_path):
    summary = pipeline.process_file(source_file(tmp_path), tmp_path/'output', key=b'x'*32)
    resolved = pipeline.finalize_file(summary, tmp_path/'resolved', key=b'x'*32,
                                     resolutions={'038': {'first_name': 'ram', 'middle_name': ''}})
    assert resolved['status'] == 'success'
    with Path(resolved['linkage_patient_file']).open(newline='') as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 1
    assert rows[0]['first_name'] == 'ram' and rows[0]['middle_name'] == ''
    assert Path(resolved['token_file']).is_file()
    assert Path(summary['cleaned_file']).read_bytes() == Path(resolved['cleaned_file']).read_bytes()
    report = json.loads(Path(resolved['report_file']).read_text())['patient_consolidation']
    assert report['patients_with_unresolved_ties'] == 0
    assert sum(report['fields_left_blank_due_to_tie'].values()) == 0
    assert report['fields_confirmed_locally']['first_name'] == 1
    assert report['fields_explicitly_left_blank']['middle_name'] == 1


@pytest.mark.parametrize('decisions', [{}, {'038': {'first_name': 'invented', 'middle_name': 'gard'}},
    {'038': {'first_name': 'ram'}}, {'038': {'first_name': 'ram', 'middle_name': 'gard', 'phone': 'bad'}}])
def test_invalid_or_incomplete_resolutions_are_rejected(tmp_path, decisions):
    summary = pipeline.process_file(source_file(tmp_path), tmp_path/'output', key=b'x'*32)
    with pytest.raises(ValueError):
        pipeline.finalize_file(summary, tmp_path/'resolved', key=b'x'*32, resolutions=decisions)
    assert not (tmp_path/'resolved'/'tokens').exists()


def test_no_conflict_runs_automatically_and_token_order_stays_fixed(tmp_path):
    summary = pipeline.process_file(source_file(tmp_path, False), tmp_path/'output', key=b'x'*32)
    assert summary['status'] == 'success'
    assert combining.COMBINATIONS[7] == ('last_dob_postcode', ('last_name','dob_iso','postcode'))
    assert combining.COMBINATIONS[8] == ('first_dob_postcode', ('first_name','dob_iso','postcode'))
    assert len(combining.COMBINATIONS) == 14


@pytest.fixture
def hospital_app(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'RUNTIME', tmp_path/'runtime')
    monkeypatch.setattr(pipeline, 'validate_configuration', lambda: b'x'*32)
    monkeypatch.setenv('PPRL_BROKER_URL','')
    return main


def upload(client, conflict=True):
    response = client.post('/api/jobs', files=[('files', ('hospital2.csv', csv_data(visits(conflict)), 'text/csv'))])
    assert response.status_code == 202
    job_id = response.json()['job_id']
    return job_id, client.get(f'/api/jobs/{job_id}').json()


def decisions(review):
    return {'revision': review['revision'], 'decisions': [
        {'local_patient_id': '038', 'field': 'first_name', 'value': 'ram'},
        {'local_patient_id': '038', 'field': 'middle_name', 'value': ''}]}


def test_api_review_resume_retry_and_download_guards(hospital_app):
    with TestClient(hospital_app.app) as client:
        job_id, job = upload(client)
        base = f'/api/jobs/{job_id}/files/001'
        assert job['status'] == 'awaiting_review'
        assert job['failed_files'] == 0 and job['awaiting_review_files'] == 1
        assert not any(k.startswith('_') for k in job['files'][0])
        assert 'downloads' not in job['files'][0]
        assert client.get(base+'/download/tokens').status_code == 409
        assert client.post(base+'/broker-submission').status_code == 409
        review = client.get(base+'/consolidation-conflicts').json()
        assert review['total'] == 1 and review['patients'][0]['local_patient_id'] == '038'
        body = decisions(review)
        bad = {**body, 'revision': 'stale'}
        assert client.post(base+'/consolidation-resolutions',json=bad).status_code == 409
        bad = decisions(review)
        bad['decisions'][0]['value'] = 'invented'
        assert client.post(base+'/consolidation-resolutions',json=bad).status_code == 422
        assert client.post(base+'/consolidation-resolutions',json=body).status_code == 202
        finished = client.get(f'/api/jobs/{job_id}').json()
        assert finished['status'] == 'success'
        assert finished['files'][0]['resolved_conflict_fields'] == 2
        download = client.get(base+'/download/tokens')
        assert download.status_code == 200
        assert len(list(csv.DictReader(io.StringIO(download.text)))) == 1
        assert client.post(base+'/consolidation-resolutions',json=body).status_code == 202
        assert client.get(base+'/download/tokens').content == download.content


def test_waiting_job_survives_restart_and_failed_resume(hospital_app, monkeypatch):
    with TestClient(hospital_app.app) as client:
        job_id, _ = upload(client)
    with TestClient(hospital_app.app) as client:
        base = f'/api/jobs/{job_id}/files/001'
        assert client.get(f'/api/jobs/{job_id}').json()['status'] == 'awaiting_review'
        review = client.get(base+'/consolidation-conflicts').json()
        original = pipeline.finalize_file
        monkeypatch.setattr(pipeline, 'finalize_file', lambda *a, **kw: (_ for _ in ()).throw(ValueError('simulated failure')))
        assert client.post(base+'/consolidation-resolutions',json=decisions(review)).status_code == 202
        item = client.get(f'/api/jobs/{job_id}').json()['files'][0]
        assert item['status'] == 'awaiting_review' and item['review_error']
        monkeypatch.setattr(pipeline, 'finalize_file', original)
        assert client.post(base+'/consolidation-resolutions',json=decisions(review)).status_code == 202
        assert client.get(f'/api/jobs/{job_id}').json()['status'] == 'success'


def test_mixed_batch_keeps_successful_file_available(hospital_app):
    with TestClient(hospital_app.app) as client:
        response = client.post('/api/jobs', files=[
            ('files', ('hospital2.csv', csv_data(visits()),'text/csv')),
            ('files', ('hospital2_clean.csv', csv_data(visits(False)),'text/csv'))])
        job_id = response.json()['job_id']
        job = client.get(f'/api/jobs/{job_id}').json()
        assert [item['status'] for item in job['files']] == ['awaiting_review','success']
        assert job['successful_files'] == 1 and job['failed_files'] == 0
        download = client.get(f'/api/jobs/{job_id}/download')
        assert download.status_code == 200
        assert len(zipfile.ZipFile(io.BytesIO(download.content)).namelist()) == 1
        base = f'/api/jobs/{job_id}/files/001'
        review = client.get(base+'/consolidation-conflicts').json()
        assert client.post(base+'/consolidation-resolutions', json=decisions(review)).status_code == 202
        download = client.get(f'/api/jobs/{job_id}/download')
        assert len(zipfile.ZipFile(io.BytesIO(download.content)).namelist()) == 2

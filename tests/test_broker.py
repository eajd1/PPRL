import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from urllib.parse import quote

from fastapi.testclient import TestClient
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from app.broker_client import make_payload, encode_record
from broker.app import main


def payload(hospital="hospital1", filename=None):
    return {"submission_id": str(uuid.uuid4()), "hospital_id": hospital,
            "filename": filename or hospital + ".csv", "token_version": "v2", "key_id": "test-key",
            "method": "hmac_sha256", "records": [
                {"patient_id": "0", "token": hashlib.sha256(b"same canonical combination").hexdigest(), "token_index": 1},
                {"patient_id": "0", "token": "b" * 64, "token_index": 2}]}


def record_headers(data, index=0):
    return {"Content-Type": "application/json", "X-Submission-ID": data["submission_id"],
            "X-Original-Filename": data["filename"], "X-Token-Version": data["token_version"],
            "X-Key-ID": data["key_id"], "X-Method": data["method"],
            "X-Token-Count": str(len(data["records"])), "X-Record-Index": str(index),
            "X-Token-Index": str(data["records"][index]["token_index"])}


def read_tokens(directory):
    records = []
    for path in directory.parent.glob('hospital[123].csv'):
        with path.open(newline="", encoding="utf-8") as handle:
            records.extend(row for row in csv.DictReader(handle) if row['submission_id'] == directory.name)
    records.sort(key=lambda row: int(row['record_index']))
    return [{key: row[key] for key in main.TOKEN_COLUMNS} for row in records]


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        certs = Path(os.environ.get("TEST_CERT_DIR", ".test-credentials"))
        self.headers, allowlist = {}, {}
        for hospital in ("hospital1", "hospital2", "hospital3"):
            pem = (certs / hospital / "client.crt").read_text()
            fingerprint = x509.load_pem_x509_certificate(pem.encode()).fingerprint(hashes.SHA256()).hex()
            allowlist[fingerprint] = hospital
            self.headers[hospital] = {"X-Proxy-Role": "hospital", "X-Client-Verify": "SUCCESS", "X-Client-Cert": quote(pem)}
        config = Path(self.tmp.name) / "allowlist.json"
        config.write_text(json.dumps(allowlist))
        self.patches = [patch.object(main, "DATA_DIR", Path(self.tmp.name) / "received"), patch.object(main, "ALLOWLIST", config)]
        for item in self.patches:
            item.start()
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()

    def post_token(self, data, index=0, headers=None):
        return self.client.post('/api/submissions', content=encode_record(data['hospital_id'], data['records'][index]),
                                headers={**(headers or {}), **record_headers(data, index)})

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_all_hospitals_receive_individual_unchanged_tokens(self):
        for hospital in self.headers:
            data = payload(hospital)
            for index in range(len(data['records'])):
                response = self.post_token(data, index, self.headers[hospital])
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()['status'], 'receiving' if index == 0 else 'received')
            stored = read_tokens(main.DATA_DIR / data['submission_id'])
            self.assertEqual(stored, [{'hospitalID': hospital, 'patientID': row['patient_id'], 'token': row['token']} for row in data['records']])
        response = self.client.get('/api/submissions', headers={'X-Proxy-Role': 'operator'})
        self.assertEqual({item['filename'] for item in response.json()}, {'hospital1.csv', 'hospital2.csv', 'hospital3.csv'})
        self.assertNotIn('patientID', response.text)
        self.assertNotIn('token', response.json()[0])

    def test_impersonation_and_operator_auth(self):
        self.assertEqual(self.post_token(payload('hospital2'), headers=self.headers['hospital1']).status_code, 403)
        self.assertEqual(self.post_token(payload()).status_code, 403)
        self.assertEqual(self.client.get('/api/submissions', headers=self.headers['hospital1']).status_code, 403)

    def test_partial_delivery_retry_conflict_and_restart(self):
        data = payload()
        first = self.post_token(data, headers=self.headers['hospital1'])
        self.assertEqual(first.json()['status'], 'receiving')
        self.assertEqual(self.post_token(data, headers=self.headers['hospital1']).status_code, 200)
        original = data['records'][0]['token']
        data['records'][0]['token'] = 'c' * 64
        self.assertEqual(self.post_token(data, headers=self.headers['hospital1']).status_code, 409)
        data['records'][0]['token'] = original
        self.client_context.__exit__(None, None, None)
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()
        response = self.post_token(data, 1, self.headers['hospital1'])
        self.assertEqual(response.json()['status'], 'received')
        self.assertEqual(response.json()['record_count'], 2)
        self.assertEqual(len(read_tokens(main.DATA_DIR / data['submission_id'])), 2)
        self.assertEqual(self.post_token(data, 1, self.headers['hospital1']).status_code, 200)
        data['filename'] = 'changed.csv'
        self.assertEqual(self.post_token(data, headers=self.headers['hospital1']).status_code, 409)

    def test_local_patient_id_and_blank_tokens_preserved(self):
        path = Path(self.tmp.name) / 'tokens.csv'
        columns = ['site_id', 'local_patient_id', 'token_version', 'key_id', 'method'] + [f'token{i}' for i in range(1, 21)]
        with path.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerow(['1', '000123', 'v2', 'test-key', 'hmac_sha256', 'a' * 64, ''] + ['b' * 64] * 18)
        with patch.dict(os.environ, HOSPITAL_ID='hospital1'):
            data = make_payload(path, 'hospital1.csv', str(uuid.uuid4()))
        self.assertEqual(len(data['records']), 20)
        self.assertTrue(all(row['patient_id'] == '000123' for row in data['records']))
        body = encode_record('hospital1', data['records'][0]).decode()
        self.assertEqual(json.loads(body), {'hospitalID': 'hospital1', 'patientID': '000123', 'token': 'a' * 64})
        self.assertEqual(data['records'][1]['token'], '')
        for forbidden in ('first_name', 'date_of_birth', 'medicare', 'PPRL_TOKEN_KEY'):
            self.assertNotIn(forbidden, body)
        path.write_text(path.read_text().replace('a' * 64, 'raw-name'))
        with patch.dict(os.environ, HOSPITAL_ID='hospital1'), self.assertRaises(ValueError):
            make_payload(path, 'hospital1.csv', str(uuid.uuid4()))

    def test_all_blank_patient_is_delivered_record_by_record(self):
        from app.broker_client import send_payload
        path = Path(self.tmp.name) / 'blank.csv'
        with path.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['local_patient_id', 'token_version', 'key_id', 'method'] + [f'token{i}' for i in range(1, 21)])
            writer.writerow(['000123', 'v2', 'test-key', 'hmac_sha256'] + [''] * 20)
        with patch.dict(os.environ, HOSPITAL_ID='hospital1'):
            data = make_payload(path, 'hospital1.csv', str(uuid.uuid4()))
        client, auth = self.client, self.headers['hospital1']

        class Connection:
            def __init__(self, *args, **kwargs):
                pass

            def request(self, method, route, body, headers):
                self.result = client.request(method, route, content=body, headers={**auth, **headers})
                self.status = self.result.status_code

            def getresponse(self):
                return self

            def read(self, size):
                return self.result.content

            def close(self):
                pass

        with patch('app.broker_client.http.client.HTTPSConnection', Connection):
            receipt = send_payload(data, url='https://broker.test:8443', context=object())
        self.assertEqual(receipt['record_count'], 20)
        self.assertEqual(receipt['status'], 'received')
        self.assertEqual(len(read_tokens(main.DATA_DIR / data['submission_id'])), 20)

    def test_extra_fields_multiple_rows_and_invalid_tokens_rejected(self):
        data = payload()
        headers = {**self.headers['hospital1'], **record_headers(data)}
        body = encode_record('hospital1', data['records'][0]).decode()
        for invalid in ('', '[' + body + ']', body.replace('patientID', 'first_name')):
            self.assertEqual(self.client.post('/api/submissions', content=invalid, headers=headers).status_code, 422)
        for token in ('raw-name', 'F' * 64):
            data['records'][0]['token'] = token
            self.assertEqual(self.post_token(data, headers=self.headers['hospital1']).status_code, 422)
        self.assertEqual(list(main.DATA_DIR.iterdir()), [])

    def test_record_list_filter_pagination_and_blank_retry(self):
        for hospital in self.headers:
            data = payload(hospital)
            data['records'][0]['token'] = ''
            for index in range(2):
                self.assertEqual(self.post_token(data, index, self.headers[hospital]).status_code, 201)
            self.assertEqual(self.post_token(data, headers=self.headers[hospital]).status_code, 200)
        auth = {'X-Proxy-Role': 'operator'}
        self.assertEqual(self.client.get('/api/records').status_code, 403)
        self.assertEqual(self.client.get('/api/records', headers=self.headers['hospital1']).status_code, 403)
        result = self.client.get('/api/records', headers=auth).json()
        self.assertEqual(result['total'], 6)
        self.assertEqual(len(result['records']), 6)
        filtered = self.client.get('/api/records?hospital_id=hospital2&limit=1&offset=0', headers=auth).json()
        self.assertEqual(filtered['total'], 2)
        self.assertEqual(filtered['records'][0]['hospitalID'], 'hospital2')
        self.assertEqual(filtered['records'][0]['token'], '')
        second = self.client.get('/api/records?hospital_id=hospital2&limit=1&offset=1', headers=auth).json()
        self.assertEqual(second['records'][0]['token'], 'b' * 64)
        self.assertEqual(self.client.get('/api/records?hospital_id=other', headers=auth).status_code, 422)
        self.assertEqual(self.client.get('/api/records?limit=101', headers=auth).status_code, 422)
        data = payload()
        self.assertEqual(self.client.post('/api/submissions', content='hospitalID,patientID,token',
                         headers={**self.headers['hospital1'], **record_headers(data), 'Content-Type': 'text/csv'}).status_code, 415)

    def test_patient_number_order_across_submissions_and_pages(self):
        for hospital in ('hospital2', 'hospital1'):
            for patient in ('10', '2', '001', 'A10', 'A2'):
                data = payload(hospital)
                for record in data['records']:
                    record['patient_id'] = patient
                # Receive combinations out of order as well as patients.
                for index in (1, 0):
                    self.assertEqual(self.post_token(data, index, self.headers[hospital]).status_code, 201)
        auth = {'X-Proxy-Role': 'operator'}
        expected = ['001', '001', '2', '2', '10', '10', 'A2', 'A2', 'A10', 'A10']
        for hospital in ('hospital1', 'hospital2'):
            rows = []
            for offset in range(0, 10, 3):
                result = self.client.get(f'/api/records?hospital_id={hospital}&offset={offset}&limit=3', headers=auth).json()
                self.assertEqual(result['total'], 10)
                rows.extend(result['records'])
            self.assertEqual([row['patientID'] for row in rows], expected)
            self.assertTrue(all(row['hospitalID'] == hospital for row in rows))
            self.assertEqual([row['token_index'] for row in rows], [1, 2] * 5)
        all_rows = self.client.get('/api/records', headers=auth).json()['records']
        self.assertEqual([row['hospitalID'] for row in all_rows], ['hospital1'] * 10 + ['hospital2'] * 10)

    def test_legacy_migration_is_complete_and_restart_safe(self):
        data = payload()
        directory = main.DATA_DIR / data['submission_id']
        (directory / 'tokens').mkdir(parents=True)
        metadata = {key: data[key] for key in main.SUBMISSION_COLUMNS}
        metadata.update(received_at='2026-10-26T00:00:00+00:00', token_count=2, record_count=2, status='received')
        main.write_csv(directory / 'metadata.csv', main.METADATA_COLUMNS, [metadata])
        for index, record in enumerate(data['records']):
            main.write_csv(directory / 'tokens' / f'{index:08d}-{record["token_index"]:02d}.csv', main.TOKEN_COLUMNS,
                           [{'hospitalID': 'hospital1', 'patientID': record['patient_id'], 'token': record['token']}])
        store = main.RecordStore()
        self.assertEqual(len(store.rows), 2)
        self.assertTrue((main.DATA_DIR / 'hospital1.csv').exists())
        self.assertFalse(directory.exists())
        self.assertTrue((main.DATA_DIR.parent / 'legacy-records' / directory.name).exists())
        restored = main.RecordStore()
        self.assertEqual(restored.rows, store.rows)
        self.assertEqual(restored.receipts[data['submission_id']]['record_count'], 2)
        self.client.app.state.store = restored
        self.assertEqual(self.post_token(data, headers=self.headers['hospital1']).status_code, 200)
        self.assertEqual(len(restored.rows), 2)


class HospitalRecoveryTests(unittest.TestCase):
    def test_interrupted_retry_is_recoverable_after_restart(self):
        from app import main as hospital_app
        certs = Path(os.environ.get('TEST_CERT_DIR', '.test-credentials'))
        with tempfile.TemporaryDirectory() as temp, patch.object(hospital_app, 'RUNTIME', Path(temp)), patch.dict(os.environ, {
            'BROKER_URL': 'https://localhost:8443', 'HOSPITAL_ID': 'hospital1',
            'BROKER_CA_FILE': str(certs / 'hospital1/ca.crt'),
            'HOSPITAL_CERT_FILE': str(certs / 'hospital1/client.crt'),
            'HOSPITAL_KEY_FILE': str(certs / 'hospital1/client.key'),
        }):
            job = {'job_id': uuid.uuid4().hex, 'status': 'success', 'files': [
                {'file_id': '001', 'filename': 'hospital1.csv', 'status': 'success',
                 'broker_status': 'sending', 'artifacts': {}}]}
            hospital_app.save_job(job)
            with TestClient(hospital_app.app) as client:
                restored = client.get('/api/jobs/' + job['job_id']).json()
                self.assertEqual(restored['status'], 'success')
                self.assertEqual(restored['files'][0]['broker_status'], 'failed')
                self.assertIn('Retry', restored['files'][0]['broker_error'])


if __name__ == '__main__':
    unittest.main()

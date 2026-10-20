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
    return {"Content-Type": "text/csv", "X-Submission-ID": data["submission_id"],
            "X-Original-Filename": data["filename"], "X-Token-Version": data["token_version"],
            "X-Key-ID": data["key_id"], "X-Method": data["method"],
            "X-Token-Count": str(len(data["records"])), "X-Record-Index": str(index),
            "X-Token-Index": str(data["records"][index]["token_index"])}


def read_tokens(directory):
    records = []
    for path in sorted((directory / "tokens").glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ['hospitalID', 'patientID', 'token']:
                raise AssertionError('Unexpected token CSV columns')
            records.extend(reader)
    return records


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

    def test_local_patient_id_and_nonblank_tokens_only(self):
        path = Path(self.tmp.name) / 'tokens.csv'
        columns = ['site_id', 'local_patient_id', 'token_version', 'key_id', 'method'] + [f'token{i}' for i in range(1, 21)]
        with path.open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerow(['1', '000123', 'v2', 'test-key', 'hmac_sha256', 'a' * 64, ''] + ['b' * 64] * 18)
        with patch.dict(os.environ, HOSPITAL_ID='hospital1'):
            data = make_payload(path, 'hospital1.csv', str(uuid.uuid4()))
        self.assertEqual(len(data['records']), 19)
        self.assertTrue(all(row['patient_id'] == '000123' for row in data['records']))
        body = encode_record('hospital1', data['records'][0]).decode()
        self.assertEqual(body.splitlines()[0], 'hospitalID,patientID,token')
        self.assertEqual(len(body.splitlines()), 2)
        for forbidden in ('first_name', 'date_of_birth', 'medicare', 'PPRL_TOKEN_KEY'):
            self.assertNotIn(forbidden, body)
        path.write_text(path.read_text().replace('a' * 64, 'raw-name'))
        with patch.dict(os.environ, HOSPITAL_ID='hospital1'), self.assertRaises(ValueError):
            make_payload(path, 'hospital1.csv', str(uuid.uuid4()))

    def test_extra_fields_multiple_rows_and_invalid_tokens_rejected(self):
        data = payload()
        headers = {**self.headers['hospital1'], **record_headers(data)}
        body = encode_record('hospital1', data['records'][0]).decode()
        for invalid in ('', body + body.splitlines()[1] + '\n', body.replace('patientID,', 'first_name,')):
            self.assertEqual(self.client.post('/api/submissions', content=invalid, headers=headers).status_code, 422)
        for token in ('', 'raw-name', 'F' * 64):
            data['records'][0]['token'] = token
            self.assertEqual(self.post_token(data, headers=self.headers['hospital1']).status_code, 422)
        self.assertEqual(list(main.DATA_DIR.iterdir()), [])


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

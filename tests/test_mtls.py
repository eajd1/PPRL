"""Integration tests use the actual production Nginx config, TLS 1.3 and broker API."""
import base64
import http.client
import json
import os
from pathlib import Path
import ssl
import csv
import subprocess
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from app.broker_client import send_payload, encode_record
from tests.test_broker import payload, record_headers, read_tokens


@unittest.skipUnless(os.environ.get('TEST_BROKER_URL'), 'Requires compose.test.yaml TLS stack')
class MutualTLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs = Path(os.environ['TEST_CERT_DIR'])
        cls.url = os.environ['TEST_BROKER_URL']
        cls.operator_url = os.environ['TEST_OPERATOR_URL']
        for attempt in range(60):
            try:
                status, _, _ = cls.request(cls.url, '/api/health', cls.context('hospital1'))
                if status == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Broker did not become healthy')

    @classmethod
    def context(cls, hospital=None):
        context = ssl.create_default_context(cafile=str(cls.certs / 'broker/ca.crt'))
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        if hospital:
            context.load_cert_chain(cls.certs / hospital / 'client.crt', cls.certs / hospital / 'client.key')
        return context

    @staticmethod
    def request(url, path, context, method='GET', data=None, headers=None):
        target = urlsplit(url)
        connection = http.client.HTTPSConnection(target.hostname, target.port, context=context, timeout=10)
        try:
            connection.connect()
            protocol = connection.sock.version()
            body = encode_record(data['hospital_id'], data['records'][0]) if data else None
            connection.request(method, path, body=body, headers={**(record_headers(data) if data else {}), **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read().decode(), protocol
        finally:
            connection.close()

    def test_three_real_clients_and_dashboard(self):
        expected = []
        for hospital in ('hospital1', 'hospital2', 'hospital3'):
            data = payload(hospital)
            expected.append(data['filename'])
            # Simulate a transfer interrupted after its first token was accepted.
            status, partial, _ = self.request(self.url, '/api/submissions', self.context(hospital), 'POST', data)
            self.assertEqual(status, 201)
            self.assertEqual(json.loads(partial)['status'], 'receiving')
            receipt = send_payload(data, url=self.url, context=self.context(hospital))
            self.assertEqual(receipt['hospital_id'], hospital)
            self.assertEqual(receipt['record_count'], len(data['records']))
            stored = read_tokens(Path('/broker-data/received') / data['submission_id'])
            self.assertEqual([row['token'] for row in stored], [row['token'] for row in data['records']])
            status, body, protocol = self.request(self.url, '/api/health', self.context(hospital))
            self.assertEqual(status, 200)
            self.assertEqual(protocol, 'TLSv1.3')
            # A retry of the identical submission is acknowledged, not inserted twice.
            status, _, _ = self.request(self.url, '/api/submissions', self.context(hospital), 'POST', data)
            self.assertEqual(status, 200)
        auth = base64.b64encode(('operator:' + os.environ['TEST_OPERATOR_PASSWORD']).encode()).decode()
        status, body, _ = self.request(self.operator_url, '/api/submissions', self.context(), headers={'Authorization': 'Basic ' + auth})
        self.assertEqual(status, 200, body)
        metadata = json.loads(body)
        self.assertTrue(set(expected).issubset({entry['filename'] for entry in metadata}))
        self.assertFalse(any('tokens' in entry for entry in metadata))
        status, records, _ = self.request(self.operator_url, '/api/records', self.context(), headers={'Authorization': 'Basic ' + auth})
        self.assertEqual(status, 200)
        result = subprocess.run(['node', 'tests/dashboard_dom.js'], input=records, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        status, html, _ = self.request(self.operator_url, '/', self.context(), headers={'Authorization': 'Basic ' + auth})
        self.assertEqual(status, 200)
        self.assertIn('PPRL Broker', html)

    def test_missing_and_untrusted_client_certificates(self):
        # Nginx may reject during handshake or return its certificate-required HTTP error.
        try:
            status, _, _ = self.request(self.url, '/api/health', self.context())
            self.assertIn(status, (400, 403))
        except (ssl.SSLError, http.client.HTTPException, ConnectionResetError):
            pass
        with tempfile.TemporaryDirectory() as temp:
            key, cert = Path(temp) / 'key.pem', Path(temp) / 'cert.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=hospital1', '-days', '1', '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
            context = self.context()
            context.load_cert_chain(cert, key)
            try:
                status, _, _ = self.request(self.url, '/api/health', context)
                self.assertIn(status, (400, 403))
            except (ssl.SSLError, http.client.HTTPException, ConnectionResetError):
                pass

    def test_identity_spoof_and_operator_auth(self):
        status, _, _ = self.request(self.url, '/api/submissions', self.context('hospital1'), 'POST', payload('hospital2'),
                                    {'X-Client-Verify': 'SUCCESS', 'X-Proxy-Role': 'operator', 'X-Client-Cert': 'forged'})
        self.assertEqual(status, 403)
        status, _, _ = self.request(self.operator_url, '/api/submissions', self.context(), headers={'X-Proxy-Role': 'operator'})
        self.assertEqual(status, 401)
        status, _, _ = self.request(self.url, '/api/submissions', self.context('hospital1'))
        self.assertEqual(status, 403)

    def test_invalid_broker_trust_hostname_and_tls12(self):
        context = ssl.create_default_context()  # Does not trust the private CA.
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(self.certs / 'hospital1/client.crt', self.certs / 'hospital1/client.key')
        with self.assertRaises(ValueError):
            send_payload(payload(), url=self.url, context=context)
        import socket
        target = urlsplit(self.url)
        with socket.create_connection((target.hostname, target.port), timeout=10) as sock:
            with self.assertRaises(ssl.SSLCertVerificationError):
                self.context('hospital1').wrap_socket(sock, server_hostname='incorrect-broker.invalid')
        context = self.context('hospital1')
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
        with self.assertRaises(ssl.SSLError):
            self.request(self.url, '/api/health', context)

    def test_ca_signed_but_not_allowlisted_certificate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # A valid certificate with even the same CN is insufficient without its pinned fingerprint.
            subprocess.run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=hospital1',
                            '-keyout', str(root / 'key.pem'), '-out', str(root / 'request.csr')], check=True, capture_output=True)
            (root / 'extensions.cnf').write_text('basicConstraints=CA:FALSE\nextendedKeyUsage=clientAuth\n')
            subprocess.run(['openssl', 'x509', '-req', '-in', str(root / 'request.csr'), '-CA', str(self.certs / 'ca/ca.crt'),
                            '-CAkey', str(self.certs / 'ca/ca.key'), '-set_serial', '989898', '-days', '1',
                            '-extfile', str(root / 'extensions.cnf'), '-out', str(root / 'cert.pem')], check=True, capture_output=True)
            context = self.context()
            context.load_cert_chain(root / 'cert.pem', root / 'key.pem')
            status, _, _ = self.request(self.url, '/api/submissions', context, 'POST', payload())
            self.assertEqual(status, 403)

    def test_three_independent_hospital_containers(self):
        import httpx
        for hospital in ('hospital1', 'hospital2', 'hospital3'):
            with httpx.Client(base_url=f'http://{hospital}-api:8000', timeout=30, trust_env=False) as client:
                for _ in range(30):
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(1)
                else:
                    self.fail(f'{hospital} container did not become healthy')
                sample = b''.join(Path('examples/hospital1.csv').read_bytes().splitlines(keepends=True)[:3])
                response = client.post('/api/jobs', files={'files': (hospital + '.csv', sample, 'text/csv')})
                self.assertEqual(response.status_code, 202, response.text)
                job_id = response.json()['job_id']
                for _ in range(120):
                    job = client.get('/api/jobs/' + job_id).json()
                    if job['status'] in ('success', 'partial_success', 'failure'):
                        break
                    time.sleep(.25)
                self.assertEqual(job['status'], 'success', job)
                item = job['files'][0]
                self.assertEqual(item['broker_status'], 'received', item)
                self.assertEqual(item['broker_receipt']['hospital_id'], hospital)

    def test_upload_clean_tokenise_and_deliver_all_three_hospitals(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app import main as hospital_app
        import csv
        for hospital in ('hospital1', 'hospital2', 'hospital3'):
            with tempfile.TemporaryDirectory() as temp, patch.object(hospital_app, 'RUNTIME', Path(temp)), patch.dict(os.environ, {
                'BROKER_URL': self.url, 'HOSPITAL_ID': hospital,
                'BROKER_CA_FILE': str(self.certs / hospital / 'ca.crt'),
                'HOSPITAL_CERT_FILE': str(self.certs / hospital / 'client.crt'),
                'HOSPITAL_KEY_FILE': str(self.certs / hospital / 'client.key'),
            }):
                with TestClient(hospital_app.app) as client:
                    # The original filename selects hospital identity using the existing cleaner.
                    sample = b''.join(Path('examples/hospital1.csv').read_bytes().splitlines(keepends=True)[:3])
                    response = client.post('/api/jobs', files={'files': (hospital + '.csv', sample, 'text/csv')})
                    self.assertEqual(response.status_code, 202, response.text)
                    job = client.get('/api/jobs/' + response.json()['job_id']).json()
                    item = job['files'][0]
                    self.assertEqual(item['status'], 'success', item)
                    self.assertEqual(item['broker_status'], 'received', item)
                    self.assertEqual(item['broker_receipt']['filename'], hospital + '.csv')
                    manifest = hospital_app.read_job(job['job_id'])
                    local_file = Path(temp) / job['job_id'] / manifest['files'][0]['artifacts']['tokens']
                    with local_file.open() as handle:
                        local = list(csv.DictReader(handle))
                    sid = item['broker_receipt']['submission_id']
                    stored = read_tokens(Path('/broker-data/received') / sid)
                    expected = [{'hospitalID': hospital, 'patientID': row['local_patient_id'], 'token': row[f'token{i}']}
                                for row in local for i in range(1, 21)]
                    self.assertEqual(stored, expected)
                    # Real retry endpoint returns the same receipt, not another submission.
                    retried = client.post(f"/api/jobs/{job['job_id']}/files/{item['file_id']}/send")
                    self.assertEqual(retried.status_code, 200)
                    self.assertEqual(retried.json()['files'][0]['broker_receipt']['submission_id'], sid)

    def test_delivery_failure_retries_saved_tokens_without_reprocessing(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app import main as hospital_app
        from app import broker_client
        attempts = []
        real_send = broker_client.send_payload

        def fail_once(data):
            attempts.append(data)
            if len(attempts) == 1:
                raise ValueError('Simulated unavailable network')
            return real_send(data)

        with tempfile.TemporaryDirectory() as temp, patch.object(hospital_app, 'RUNTIME', Path(temp)), patch.dict(os.environ, {
            'BROKER_URL': self.url, 'HOSPITAL_ID': 'hospital1',
            'BROKER_CA_FILE': str(self.certs / 'hospital1/ca.crt'),
            'HOSPITAL_CERT_FILE': str(self.certs / 'hospital1/client.crt'),
            'HOSPITAL_KEY_FILE': str(self.certs / 'hospital1/client.key'),
        }), patch.object(broker_client, 'send_payload', side_effect=fail_once), patch.object(hospital_app.pipeline, 'process_file', wraps=hospital_app.pipeline.process_file) as processing:
            with TestClient(hospital_app.app) as client:
                response = client.post('/api/jobs', files={'files': ('hospital1.csv', b''.join(Path('examples/hospital1.csv').read_bytes().splitlines(keepends=True)[:3]), 'text/csv')})
                job_id = response.json()['job_id']
                job = client.get('/api/jobs/' + job_id).json()
                self.assertEqual(job['files'][0]['status'], 'success')
                self.assertEqual(job['files'][0]['broker_status'], 'failed')
                retried = client.post(f'/api/jobs/{job_id}/files/001/send')
                self.assertEqual(retried.status_code, 200)
                self.assertEqual(retried.json()['files'][0]['broker_status'], 'received')
                self.assertEqual(processing.call_count, 1)
                self.assertEqual(attempts[0], attempts[1])


if __name__ == '__main__':
    unittest.main()

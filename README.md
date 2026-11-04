# Hospital PPRL and PostgreSQL matching broker

The original application is retained: local hospital CSV processing, record-wise JSON delivery, the 25-token
v3 scheme, PostgreSQL matching, global patient IDs and the React broker-dashboard.
Security from the security branch is integrated around that workflow.

```text
Hospital browser -> local hospital API -> cleaning -> combinations -> HMAC
  -> individual JSON token records over HTTPS/TLS 1.3 + hospital client certificate + service credential
  -> broker Nginx :8443 -> private Unix socket -> broker API -> PostgreSQL matching
Operator browser -> HTTPS :9443 -> original React broker-dashboard + admin token
```

Nginx verifies the hospital certificate against the CA and replaces identity
headers. The API checks the certificate's SHA-256 fingerprint allowlist and
requires the service credential and JSON hospital identity to agree. Hospital and
administrator routes are separated. The broker has no TCP listener or published
API port; PostgreSQL is on an internal network with no host port. The broker has
network access to its database. Its React frontend and matching APIs are unchanged.

## Start

Existing credentials and certificates are preserved. On a fresh checkout only:

```powershell
python backend/scripts/setup_demo.py
python scripts/generate_certificates.py --broker-host broker-proxy --broker-host localhost --broker-host 127.0.0.1
```

Then:

```powershell
certutil -user -addstore Root certs/unified/ca/ca.crt
docker compose up --build -d
```

- Hospital: http://localhost:8082
- Original broker dashboard: https://localhost:9443
- Dashboard login: `BROKER_ADMIN_TOKEN` from `.env.admin` in the dashboard form.

Upload the synthetic CSVs in `backend/dataset/`, then use **Send to broker** and
**View / refresh broker results**. Hospital raw data stays local; individual JSON token records are
submitted over mTLS, staged durably, and matched in PostgreSQL once complete. Saved hospital jobs remain in
`backend/runtime`; the existing PostgreSQL volume is retained. Temporary
record-only CSV storage is not used by the matching application.

The file structure remains `backend/`, `frontend/`, `broker-dashboard/` and
`broker/` (TLS proxy configuration). See [broker/README.md](broker/README.md) for details.

## Verify

```powershell
python -m pytest -q
python scripts/check_secure_broker.py
```

Database integration tests require a separate disposable `pprl_test*` database.
They skip when `BROKER_TEST_DATABASE_URL` is not set. Stop with
`docker compose down`; do not add `--volumes` unless deliberately deleting data.

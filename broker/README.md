# Four-laptop secure PPRL demonstration

Each hospital runs the existing application locally. Only its backend connects to
the broker. The browser uploads to `http://localhost:8080`; it never holds client
keys or HMAC secrets. Cleaning, canonicalisation and the existing HMAC-SHA-256
implementation are unchanged.

## Architecture

```text
Hospital browser -> local hospital API -> cleaning -> combinations -> HMAC
    -> HTTPS, TLS 1.3, hospital client certificate -> broker Nginx :8443
    -> private Unix socket -> broker FastAPI -> record storage -> acknowledgement
Operator browser -> HTTPS :9443 + operator login -> record dashboard
```

Nginx requires a CA-verified client certificate. FastAPI maps the SHA-256
fingerprint of that verified certificate to an identity in `allowlist.json`.
It rejects a body `hospital_id` that differs from that identity. Certificate CNs
and incoming identity headers do not establish identity. Nginx overwrites the
identity headers. The backend has **no TCP listener and no network interface**;
only Nginx shares its Unix socket. Do not publish the API through another proxy
or start it with a TCP listener: its proxy headers are trusted only on that socket.

The hospital processes CSVs locally and sends **one JSON record per HTTPS request**:

```json
{"hospitalID":"hospital1","patientID":"000123","token":"<64-character HMAC token>"}
```

No CSV is transferred between hospital and broker. `hospitalID` must match the
verified client certificate. `patientID` preserves the local patient ID, including
leading zeroes. Every patient produces 20 records, one per combination. Blank
combinations are included with `"token":""`; they are never skipped or hashed
into a shared missing-value token. Nonblank HMAC values remain unchanged.

Submission ID, original filename, scheme version, key ID, token position, record
index and expected count travel as HTTP headers for validation and safe retries.
Raw identifiers, combinations, reports and HMAC secrets stay at the hospital.
Keep original filenames free of patient information.

The operator dashboard shows received hospital IDs, patient IDs and full token
values, with Hospital 1/2/3 filtering and pagination. It refreshes every three
seconds and includes records from partially delivered submissions. Matching is
not implemented. Broker storage remains local CSV files; transport is JSON.

## Prerequisites and LAN addresses

Install Docker Desktop with Linux containers on all four Windows laptops. Use
the same checkout/version on the three hospitals. The provisioning laptop also
needs Python 3 and OpenSSL 3 (`python --version`, `openssl version`). For example,
use OpenSSL included with Git for Windows and add its `usr\bin` directory to PATH.
The generator supplies its own OpenSSL configuration file.

Connect all laptops to the same private LAN. Disable Wi-Fi client/AP isolation;
guest Wi-Fi often prevents laptop-to-laptop access. On the broker run `ipconfig`
and reserve its IPv4 address in DHCP. The examples below use **192.168.1.50**;
replace it everywhere with the actual broker address. `localhost` always means
the laptop you are currently using, never the remote broker.

Using a LAN IP directly is simplest. If using `broker.pprl.test`, configure local
DNS, or add `192.168.1.50 broker.pprl.test` to each Windows hosts file
(`C:\Windows\System32\drivers\etc\hosts`, edited as Administrator). Containers
do not necessarily inherit the Windows hosts file: add
`extra_hosts: ["broker.pprl.test:192.168.1.50"]` under the hospital `backend`
service in a local Compose override, or use LAN DNS. The certificate SAN must
contain the exact IP/hostname used in `BROKER_URL`.

## Generate and distribute credentials

On a trusted provisioning laptop, from the repository root in PowerShell:

```powershell
python scripts/generate_certificates.py --broker-host 192.168.1.50 --broker-host broker.pprl.test --broker-host localhost --broker-host 127.0.0.1
```

Enter a unique operator password of at least 12 characters. The dashboard login
username is `operator`. The password is hashed using SHA-512 crypt for Nginx;
it is not written in plaintext or passed as an OpenSSL command argument.
The script refuses to overwrite a nonempty output directory. `--output` selects
a fresh directory. Certificates last 90 days; the demonstration CA lasts a year.

Generated layout:

```text
certs/ca/         ca.key, ca.crt (keep offline; never distribute ca.key)
certs/broker/     broker.key, broker.crt, ca.crt, allowlist.json, operator.htpasswd
certs/hospital1/  client.key, client.crt, ca.crt
certs/hospital2/  client.key, client.crt, ca.crt
certs/hospital3/  client.key, client.crt, ca.crt
```

Transfer each bundle securely to its intended laptop only. Each hospital must
receive only its own client bundle. The broker gets only the broker bundle;
it must not receive hospital HMAC secrets, client private keys, or the CA key.
Restrict Windows file ACLs on private keys to the deployment account and Docker.
The hospital container runs as UID 10001; on Linux hosts set ownership/read access
for that UID explicitly (do not make private keys world-readable). Windows Docker
Desktop bind mounts use Windows permissions. Keys are mounted read-only and
excluded from Git and Docker build contexts. Keep backups and received CSV files
under appropriate machine access controls; use BitLocker for disk protection.

To revoke a hospital certificate, remove its fingerprint from the broker
allowlist and restart `broker-api`. To rotate certificates, generate a fresh
credential set, distribute the appropriate new bundles, replace the allowlist,
and restart services. Do not overwrite a live CA casually: changing the CA
requires distributing the new trust certificate to all participants.

## Laptop 4: broker

Copy only its credential bundle to `certs/broker/`. Never copy a hospital `.env`
to this machine. The broker Compose file does not load the hospital `.env` into
its API. Configure deployment variables separately:

```powershell
if (-not (Test-Path .env.broker)) { Copy-Item broker/.env.example .env.broker }
docker compose --env-file .env.broker -f compose.broker.yaml up -d --build
```

Only port **8443** is exposed to the LAN. Port **9443** binds to loopback by
default so the operator dashboard is local to Laptop 4. No HTTP port or FastAPI
port is published. Received data is bind-mounted to `broker/data/` inside the
project directory. Only the private Unix socket uses a named Docker volume.
No database is used. On Linux hosts, create `broker/data/` and give UID 10001
write access before starting; Windows Docker Desktop uses Windows file permissions.

In elevated PowerShell, permit the submission port on the private local subnet:

```powershell
New-NetFirewallRule -DisplayName 'PPRL broker mTLS' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -Profile Private -RemoteAddress LocalSubnet
```

Open **https://localhost:9443** on Laptop 4 and sign in as `operator`. Import the
public `certs/broker/ca.crt` into the operator account's **Trusted Root
Certification Authorities** store (never import `ca.key`). For example:

```powershell
Import-Certificate -FilePath certs/broker/ca.crt -CertStoreLocation Cert:\CurrentUser\Root
```

Restart the browser after importing the CA. Do not bypass certificate warnings.
Hospital Python clients use the explicitly configured CA file, not the Windows
browser trust store. They verify broker hostname and validity as well as trust.

For a remote operator, set `DASHBOARD_BIND_IP` to the broker LAN IP, permit 9443
only from that operator's address, use a certificate SAN covering that address
or hostname, and import the CA on the operator laptop. Login is still mandatory.
This is a small LAN prototype; do not expose either endpoint to the Internet.

## Laptops 1, 2 and 3: hospitals

On **each** laptop copy `.env.example` to `.env` if needed. Preserve an existing
`.env` rather than overwriting an already provisioned HMAC key. Securely provision
the same random 32-byte-or-longer HMAC key, key ID and scheme version to all three
hospitals. Generate a key once on a trusted hospital provisioning machine with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Transfer it securely to the hospitals, never to the broker. Set these entries in
each hospital `.env` (replace the placeholder and broker address):

```dotenv
PPRL_TOKEN_KEY=<same securely provisioned hex key on all hospitals>
PPRL_KEY_ID=pprl-linkage-2026-01
PPRL_TOKEN_VERSION=v2
HOSPITAL_ID=hospital1
BROKER_URL=https://192.168.1.50:8443
HOSPITAL_TLS_DIR=./certs/hospital1
```

| Laptop | HOSPITAL_ID | HOSPITAL_TLS_DIR  | Upload example |
| ------ | ----------- | ----------------- | -------------- |
|   1    |  hospital1  | ./certs/hospital1 |  hospital1.csv |
|   2    |  hospital2  | ./certs/hospital2 |  hospital2.csv |
|   3    |  hospital3  | ./certs/hospital3 |  hospital3.csv |

Ensure the CSV's hospital ID agrees with that laptop. Existing inference from
`hospital1.csv`, etc. still works. A deployment configured as hospital1 cannot
process a hospital2 dataset for delivery. Original filenames are preserved.
Files with explicit `hospital_id` can use other simple `.csv` filenames.

On all three hospital laptops:

```powershell
Test-NetConnection 192.168.1.50 -Port 8443
docker compose -f compose.yaml -f compose.hospital.yaml up -d --build
```

Open **http://localhost:8080**, upload that hospital's CSV and select **Send to
broker**. Cleaning and HMAC generation run in the background. The card shows
**Received by broker** only after the broker returns an acknowledgement. Check
the dashboard on Laptop 4 for the real filename and originating hospital.
Hospital UI/API port 8080 remains loopback-only and needs no LAN firewall rule.

Without `compose.hospital.yaml` and without `BROKER_URL`, the original local
preparation mode still works and reports that no files were sent.

## Failure and duplicate behaviour

Transport errors are retried up to three times using the same submission UUID
(derived from hospital identity, job UUID and file ID). An identical retry returns
the existing receipt; reused IDs with different content or hospital identity are
rejected with HTTP 409. Each token has a stable index within its original file submission. The single-worker
broker appends the record to its hospital CSV before acknowledging it. A retry of an
already saved index returns the existing result; different content at that index
is rejected. The receipt stays `receiving` until every expected token is saved.
Keep exactly one broker worker/process; the duplicate lock is in-process.
Separate new uploads create separate submissions, even with identical filenames.

TLS verification errors fail closed; no plaintext fallback or redirects are
followed. Successful local preparation is preserved if delivery fails. Select
**Retry sending** to resend the saved tokens, without re-uploading or regenerating
them. A fresh page retrieves the saved receipt/status. An interrupted process
may require retry after restart; retrying the same UUID is safe if the broker had
already committed. Cleaning failures still appear separately from delivery failures.

## API and storage

| Endpoint | Access | Result |
| --- | --- | --- |
| POST :8443/api/submissions | CA-verified, allowlisted hospital certificate | 201 new token; 200 identical token retry |
| GET :8443/api/health | CA-verified, allowlisted hospital certificate | Broker health |
| GET :9443/api/submissions | Operator HTTPS Basic authentication | Latest 500 submission receipts |
| GET :9443/api/records | Operator HTTPS Basic authentication | Paginated records; optional hospital_id, offset and limit |
| GET :9443/api/health | Operator authentication | Broker health |
| GET :9443/ | Operator authentication | Auto-refreshing dashboard |

The hospital sends one `application/json` object per record, containing exactly
`hospitalID`, `patientID` and `token`. CSV requests and multi-record requests are
rejected. API acknowledgements and dashboard responses are JSON. Local broker
storage uses CSV; there is no database.

Project-directory file layout:

```text
backend/runtime/<job-id>/files/<file-id>/
    input/<original-filename>.csv
    output/cleaned/<stem>_cleaned.csv
    output/combinations/<stem>_combinations.csv
    output/tokens/<stem>_tokens.csv
broker/data/received/
    hospital1.csv
    hospital2.csv
    hospital3.csv
```

Each hospital CSV contains all its received records, including hospital ID,
patient ID, token, submission metadata and stable record/combination indexes.
Files are created when a hospital first sends data. New records are appended and
flushed to disk before acknowledgement. Receipts and sorted dashboard records
are indexed in memory and rebuilt from these CSVs on startup, so refreshing the
dashboard does not read thousands of individual files.

Existing per-record files are automatically merged on startup. After the merged
CSVs are committed, the old submission directories are moved to
`broker/data/legacy-records/` as a backup; they are no longer used by the broker.
Restarting migration does not duplicate records.

If delivery is interrupted, saved tokens remain. Retry resends the same indexed
tokens, acknowledges identical ones without duplication, and fills in the missing
ones. Receipts are reconstructed from hospital CSV rows after restart. Changed metadata or token content for an existing index is rejected.
Extra JSON fields, multiple records in a single request, malformed nonblank tokens and
hospital identities that differ from the verified certificate are rejected.

The hospital retains its existing patient-linkage CSV and local diagnostic
reports/job manifests in their existing formats. Broker processing beyond
receipt/storage is TODO; no matching is performed.

Both data directories are excluded from Git and Docker image contents. They
survive container removal because they live inside the project directory.
Deleting these host folders deletes the saved files. Broker processing beyond
receipt/storage is **TODO**.

## Automated checks

From the root on a development machine with Python, OpenSSL and Docker Desktop:

```powershell
python scripts/test_secure_broker.py
```

Docker Compose **2.24.4+** is required for the test-only `!override` port removal.
The script creates ignored, disposable test credentials with a known test-only
operator password, builds an isolated `pprl-security-test` stack, runs tests,
and removes only that stack and its test volumes. It does not use or delete the
production CSV folders or volumes. Never use `.test-credentials` for deployment.

Checks cover three independent production hospital containers, actual CSV processing and transmission,
unchanged tokens in received CSV files, local patient ID preservation and exclusion of other raw identifiers, strict schema,
real records rendered by the dashboard JavaScript, duplicate/conflict handling,
operator authentication, forged headers/body identity, no client certificate,
untrusted and CA-signed-but-unlisted client certificates, wrong broker trust and hostname, and TLS 1.2
rejection through the actual production Nginx configuration. The dashboard DOM
test executes its renderer in Node; it is not a full browser visual test.

Run existing HMAC tests separately with the project virtual environment:

```powershell
.venv\Scripts\python.exe -m unittest discover -s backend/src/hospital -p test_*.py -v
```

The container tests exercise real sockets and TLS, but cannot establish physical
four-laptop connectivity. On the actual laptops verify LAN routing/AP isolation,
Windows firewall behaviour, Docker file permissions, hostname resolution,
browser CA trust, receipt display from all three independent deployments, and
rejection when a hospital client bundle is removed or replaced.

TLS configuration follows the official [Nginx SSL module documentation](https://nginx.org/en/docs/http/ngx_http_ssl_module.html)
and Python's [SSLContext documentation](https://docs.python.org/3.12/library/ssl.html).

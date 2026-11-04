# PostgreSQL broker and dashboard

The broker receives individual protected JSON token records from hospital backends and matches each completed submission. It stores stable global patient IDs, hospital/local-ID mappings, token history, receipt times and review evidence. This release is for local synthetic-data demonstrations.

This is the single broker guide. Run the commands below from the repository root. `broker/` contains the TLS proxy configuration,
`backend/src/broker/` contains the PostgreSQL matching API, and `broker-dashboard/`
contains the React frontend.

Hospital submissions use one JSON token record per HTTPS request over mTLS. No CSV is transferred by the hospital client. Broker acknowledgements, status and matching results are JSON. Raw patient CSVs, token CSV artifacts and intermediate files remain at the hospital.

## Start the application

The original React `broker-dashboard`, PostgreSQL persistence, matching, reviews
and global patient IDs remain intact. Security practices from the security branch
are applied to this implementation; the branch's record-only receiver and UI are
not deployed.

```text
Hospital -> JSON token records over HTTPS/TLS 1.3 + client certificate + service credential
  -> Nginx :8443 -> private Unix socket -> original API -> PostgreSQL matching
Operator -> HTTPS :9443 -> original React dashboard + administrator token
```

On a fresh checkout only, provision coordinated credentials and certificate bundles:

```powershell
python backend/scripts/setup_demo.py
python scripts/generate_certificates.py --broker-host broker-proxy --broker-host localhost --broker-host 127.0.0.1
```

Both scripts refuse to overwrite existing credentials. Preserve the existing
HMAC key, database credentials, administrator token and PostgreSQL volume.

```powershell
certutil -user -addstore Root certs/unified/ca/ca.crt
docker compose up --build -d
```

Hospital UI: http://localhost:8082. Dashboard: https://localhost:9443. Paste the
`BROKER_ADMIN_TOKEN` value from `.env.admin` into the original dashboard form.
No additional HTTP Basic/operator login is used.

Nginx requires a CA-verified hospital client certificate and overwrites proxy
identity headers. FastAPI uses the SHA-256 fingerprint allowlist, not the CN, to
identify the hospital. That identity must match both the existing service
credential and the JSON `hospitalID`. Missing certificates, unlisted fingerprints
and mismatched identities fail closed. Administrator routes are only proxied by
the HTTPS dashboard listener and still require its administrator token.

The broker listens only on `/run/pprl/broker.sock`; no API TCP port is published.
Only Nginx and the broker mount the socket volume. The broker retains network
access to PostgreSQL on an internal Docker network. PostgreSQL has no host port.
Matching and result APIs are retained. The hospital uses the record endpoint described below; the older CSV endpoint remains available for compatibility with existing tools.

Certificate private keys and CA bundles are mounted read-only. The CA signing
key stays on the provisioning machine and is never mounted in a container.
The local demo mounts all three hospital bundles. Separate hospitals should
receive only their own bundle and service credential.

For a separate hospital, set numeric `HOSPITAL_ID`, `HOSPITAL_TLS_DIR` and
`PPRL_BROKER_URL=https://BROKER_HOST:8443`, then run:

```powershell
docker compose -f compose.yaml -f compose.hospital.yaml up --build -d backend frontend
```

For a broker-only host:

```powershell
docker compose -f compose.yaml -f compose.broker.yaml up --build -d postgres broker broker-proxy
```

For LAN/internet access, issue the broker certificate with `--broker-host` for its
actual IP or DNS name. Set `BROKER_BIND_IP=0.0.0.0` to accept hospital connections.
Keep the dashboard local by default, or explicitly set `DASHBOARD_BIND_IP` to the
required interface for HTTPS administrator access. Keep PostgreSQL and the API
unpublished. Use valid matching certificate names and firewall access appropriate
to the intended hospital clients. Never bypass CA or hostname verification.

Read-only live checks:

```powershell
python scripts/check_secure_broker.py
```

## Record-wise hospital transport

The hospital reads its generated token CSV locally and sends one request per token:

```http
POST /api/v1/submissions/records
Content-Type: application/json
```

```json
{"hospitalID":"hospital1","patientID":"000123","token":"<64-character HMAC token>"}
```

All existing 25 token positions are preserved, including blanks as `"token":""`.
Patient IDs remain strings, including leading zeroes. No token values are rehashed.
The service credential and mTLS certificate must identify the same hospital as
`hospitalID`. The shared HMAC key never leaves the hospital.

`Idempotency-Key`, `X-Submission-Digest`, `X-Original-Filename`, `X-Token-Version`,
`X-Key-ID`, `X-Method`, `X-Record-Index` (zero-based), `X-Token-Index` (1?25) and
`X-Token-Count` carry batch identity and metadata. Expected count is patients ? 25.
Each acknowledgement includes the existing submission ID plus received/expected
record counts. Filenames should contain no patient information.

PostgreSQL durably stages each record. Identical retries are idempotent; changed
content or metadata under the same identifier returns 409. Partial transfers
survive restarts and remain `receiving`, excluded from matching. After all records
arrive, the broker checks the complete digest and existing token validation,
assembles the original matching payload, and queues the unchanged matching worker.
A failed matching run can be requeued with the same identifier.

The hospital schedules delivery in the background so slow networks do not hold
open the browser request. Existing send/retry buttons and result polling continue
to work without frontend changes. A retry resends the same indexed records;
previously stored records are acknowledged without duplication.

The `0002_record_transport` migration adds only transport staging tables and keeps
existing submissions, patient IDs, matching history and database data intact.

## Verification

All 54 tests passed inside Docker against a disposable PostgreSQL database,
including partial-transfer persistence across restarts, unchanged token replay,
metadata conflicts, invalid hospital identities, numeric patient-ID rejection,
blank-token preservation, digest verification and matching only when complete.
The test database was removed afterward.

A live hospital upload delivered 50 individual JSON requests over mTLS for two
patients, completed PostgreSQL matching and returned global patient IDs. Retrying
reused the same submission. The original React dashboard and administrator-token
API passed their live checks. Dashboard and hospital frontend source are unchanged.

## What changed

| Area | Changes |
|---|---|
| `backend/src/broker` | FastAPI API, hospital/admin authentication, strict CSV validation, PostgreSQL persistence, queued processing, weighted scoring and patient grouping |
| `backend/migrations` | Versioned Alembic migration with a frozen SQL schema |
| `backend/Broker.Dockerfile` | Builds the React dashboard and runs it with the Python broker; no Node runtime, hospital data or HMAC configuration |
| `broker-dashboard` | React/TypeScript/Vite dashboard using official shadcn/ui components, with patient details, version history, submissions and read-only reviews |
| `backend/src/hospital/broker_client.py` | Server-to-server token submission, scoped result retrieval and persisted delivery state |
| `backend/app/main.py` | Submission/result routes and delivery status in saved jobs |
| `frontend` | Send/retry controls, paginated broker results and updated privacy copy |
| Hospital token pipeline | Incomplete combinations are blank; scheme defaults to `v3`; original 20 rules are unchanged, with five private-healthcare rules appended as tokens 21–25 |
| Dependencies | Hospital and broker share FastAPI/multipart versions; hospital adds HTTPX; broker adds SQLAlchemy, psycopg and Alembic. Corrected the unavailable dotenv pin to the tested version. |
| `compose.yaml` | Separate broker and PostgreSQL 17 services, health checks and a persistent database volume |

Existing `v2` exports must be regenerated. The broker accepts `v3` only; do not mix schemes or silently reinterpret existing exports. The hospital's pre-existing majority/tie consolidation behaviour remains unchanged.

The submission CSV has exactly 30 columns: `site_id,local_patient_id,token_version,key_id,method,token1,...,token25`. The three metadata fields identify the hashing scheme, key label (never the secret) and algorithm. Exports missing metadata or containing only 20 tokens must be regenerated by uploading and processing the original hospital CSV again. Do not edit existing token headers to upgrade a file.

After updating the code, run `docker compose up --build -d backend frontend broker`. This preserves the named volumes. Existing stored 20-token snapshots remain valid for their original positions; old validated queued submissions can finish with blank private-healthcare positions. New submissions require all 25 token columns. No database migration or global-ID reset is required.

The token scheme stays `v3` because the first 20 combination definitions, order and hash inputs are unchanged. The scoring version advances to `grouped-24-v2` to identify decisions that include private-healthcare evidence. Earlier assessments remain historical and are not rescored automatically.

## Matching behaviour

| Token group | Weights in token order | Maximum |
|---|---|---:|
| 1–4 | 4, 2, 4, 4 | 4 |
| 5–7 and 21–23 | 5, 2, 5 and 5, 2, 5 | 5 |
| 8–11 | 3, 3, 3, 1 | 3 |
| 12–14 | 4, 3, 4 | 4 |
| 15–18 | 2, 1, 2, 2 | 2 |
| 19–20 and 24–25 | 6, 6 and 6, 6 | 6 |

Each group contributes only its strongest matching rule. Scores range from 0 to 24. Blank tokens never match. Compatibility requires equal token scheme, key ID and method. Score 12 or higher from at least two groups proposes a match; 6–11 requires review. These provisional scores are not probabilities, and different groups still share underlying identifiers.

A new hospital record joins a global patient only if it meets the threshold against every existing member. Multiple plausible groups, inconsistent evidence or a second local ID from one hospital are held for review. Below-review evidence creates a separate global patient. An all-blank record gets a global ID marked insufficient evidence. Existing global patients are never merged automatically.

An already linked hospital/local ID retains its global ID. Consistent updates become the matching version. Conflicting updates are saved and flagged while the prior consistent version remains active for matching. Reassessment supersedes the prior review case but does not imply a human approval. No manual resolution controls are provided.

Times are stored as UTC and shown in the browser's timezone. Latest receipt and latest content change are separate. A retry does not refresh receipt time; a new submission does. Receipt time is not a clinical update date. Hospital result pages show the immutable result of that submission; the broker patient screen shows the current membership state.

## APIs and isolation

Hospital endpoints require a bearer service credential and an approved mTLS certificate. Record transport uses JSON and the headers documented above. The compatibility CSV endpoint uses multipart field `file` and `Idempotency-Key`. Duplicate identifiers with different bytes return 409. Replayed requests return the original submission. A failed matching transaction can be requeued by resending the same file and identifier.

- `POST /api/v1/submissions/records` (hospital JSON transport)
- `POST /api/v1/submissions` (compatibility CSV API)
- `GET /api/v1/submissions/{id}`
- `GET /api/v1/submissions/{id}/results?offset=0&limit=20`

Administrator-only endpoints include `/api/v1/admin/overview`, `/patients`, `/patients/{id}`, `/records/{id}/versions`, `/submissions`, `/submissions/{id}`, `/reviews` and `/reviews/{id}`. Lists are paginated. Patients can be searched by global or local patient ID. Submissions support status filtering. Tokens are never returned in dashboard responses; review evidence contains rule positions and scores.

Hospital routes are `/api/jobs/{job}/files/{file}/broker-submission` and `/broker-results`. Hospital A cannot retrieve hospital B's submissions through the broker API. The existing hospital application has no human-user authentication and its demo configuration can hold all three service credentials; this is not a production hospital isolation boundary.

The broker validates declared format and compatibility; it cannot prove that a hospital generated a token correctly. Full OIDC, remote HTTPS deployment, clinical-record requests, model training and human match resolution are outside this release.

## Queue, database and recovery

Validated input is durably staged in PostgreSQL before the API returns 202. One in-process worker claims work under a PostgreSQL advisory transaction lock. Each submission's patient records are processed in stable local-ID order. Memberships, versions, assessments, audit events and results commit together. On process termination, the transaction rolls back and the queued submission is recoverable. A caught processing failure rolls back partial changes and marks the submission failed for an explicit retry.

PostgreSQL is not published on a host port. The application uses SQLAlchemy/psycopg and Alembic; migration runs before Uvicorn starts. The first migration's SQL is independent of future ORM changes. `docker compose down` preserves named volumes. Do not add `--volumes` unless intentionally deleting stored demonstration data.

For a backup, avoid piping binary dumps through Windows PowerShell. Create the dump inside the database container and copy it out:

```powershell
docker compose exec -T postgres pg_dump -U pprl -d pprl -Fc -f /tmp/pprl.dump
docker compose cp postgres:/tmp/pprl.dump ./pprl.dump
```

Restore into a **new** database for verification:

```powershell
docker compose cp ./pprl.dump postgres:/tmp/pprl-restore.dump
docker compose exec -T postgres createdb -U pprl pprl_restore
docker compose exec -T postgres pg_restore -U pprl -d pprl_restore --no-owner /tmp/pprl-restore.dump
```

Backups contain sensitive linkage information. Keep them out of Git and retain access controls.

## Verification

Install the hospital and broker requirements plus pytest into a Python 3.12 virtual environment. Run from the project root:

```powershell
$env:PYTHONPATH = "$PWD/backend"
$env:BROKER_TEST_DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@localhost:5432/pprl_test"
python -m pytest backend/tests backend/src/hospital/test_token_generator.py -q
```

The integration fixture requires a disposable database whose name starts with `pprl_test`. It migrates and clears that database between tests. Without a test database URL, integration tests are explicitly skipped. Never point it at a retained database.

Dashboard build:

```powershell
cd broker-dashboard
npm ci
npm run build
```

Browser tests use an installed Chrome and running local services. Set `BROKER_UI_URL` and `HOSPITAL_UI_URL` (defaults are temporary test ports 18081/18080), use an empty broker database, and run `npx playwright test` from `broker-dashboard`. The credential is read from `BROKER_UI_TOKEN` or the local `.env.admin`. Tests upload synthetic data, send tokens, inspect patient history/reviews, verify filtering, mobile layout and credential loss on reload.

For synthetic evaluation, create a separate empty database named `pprl_eval*`, set `BROKER_EVAL_DATABASE_URL` and run `python backend/scripts/evaluate_demo.py`. It verifies positional row counts, consistent identity mappings, per-hospital uniqueness and the supplied generator's modulo mapping before evaluating. Reports and regenerated v3 artifacts go under `backend/runtime/evaluation`.

The supplied dataset evaluation produced 182 hospital patient records, 27 pending reviews, 11 true-positive pairs, zero false-positive pairs and 91 false-negative pairs: **100% precision on 11 predicted pairs and 10.78% recall**. The review rate was **14.84%**. This small synthetic result demonstrates conservative behaviour, not validated clinical performance. Thresholds were left unchanged.


## Verification performed during implementation

- 33 Python tests passed under Python 3.12 with the pinned application requirements, using an isolated PostgreSQL 18 cluster available on the development machine.
- 2 headless Chrome tests passed against live hospital and broker services, including three hospital uploads and submissions, dashboard evidence inspection, reload sign-out, filtering and mobile layout.
- Production TypeScript/Vite build passed; rendered desktop and mobile screens were inspected.
- Alembic schema matched the application models; PostgreSQL dump/restore preserved all table counts.
- Docker Compose configuration passed validation. Docker Desktop could not start its engine, so container image builds, PostgreSQL 17 execution and Docker volume recreation remain unverified on this machine.

The Docker deployment explicitly selects PostgreSQL 17. Re-run the integration suite against PostgreSQL 17 and the Compose smoke workflow when Docker is available. The local PostgreSQL 18 tests are not a substitute for that final deployment check.

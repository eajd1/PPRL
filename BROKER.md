# PostgreSQL broker and dashboard

The broker receives protected token CSVs from hospital backends and immediately matches each accepted submission. It stores stable global patient IDs, hospital/local-ID mappings, token history, receipt times and review evidence. This release is for local synthetic-data demonstrations.

## Start the application

From the project root, generate coordinated demo credentials once:

```powershell
python backend/scripts/setup_demo.py
docker compose up --build -d
```

The setup script refuses to overwrite existing configuration. It creates `.env` (hospital HMAC key and service credentials), `.env.broker` (database connection and credential hashes), `.env.postgres` (database credentials), and `.env.admin` (the administrator credential). These files are ignored by Git and excluded from Docker build contexts. Do not share them.

- Hospital application: http://localhost:8080
- Broker dashboard: http://localhost:8081
- Broker API reference: http://localhost:8081/docs

Copy the `BROKER_ADMIN_TOKEN` value from `.env.admin` into the dashboard credential field. The browser holds it only in memory; refreshing the page signs out. Service credentials are only used by hospital backends. The broker container never receives the hospital `.env` or HMAC key.

Upload the three synthetic CSVs from `backend/dataset`, wait for processing, then select **Send to broker** on each successful file. The result card shows arrival and matching status, with paginated local/global patient IDs. A delivery failure does not change hospital processing success. Retry uses the same submission identifier, including after a lost response. A fresh upload/job creates an intentional new submission.

For existing configuration, preserve your HMAC key. Set `PPRL_TOKEN_VERSION=v3`, add `PPRL_BROKER_URL=http://broker:8000` and a JSON `PPRL_BROKER_CREDENTIALS` map to `.env`. Use the broker and PostgreSQL example files for separate configuration. `BROKER_HOSPITAL_TOKEN_HASHES` maps each hospital ID to the SHA-256 hexadecimal digest of its unique random service credential; `BROKER_ADMIN_TOKEN_HASH` is the corresponding digest of a different random administrator credential. Never reuse the HMAC key as a service credential. Key IDs must match `BROKER_KEY_IDS`.

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
| Hospital token pipeline | Incomplete combinations are blank; scheme defaults to `v3`; original 20 rule definitions and ordering are unchanged |
| Dependencies | Hospital and broker share FastAPI/multipart versions; hospital adds HTTPX; broker adds SQLAlchemy, psycopg and Alembic. Corrected the unavailable dotenv pin to the tested version. |
| `compose.yaml` | Separate broker and PostgreSQL 17 services, health checks and a persistent database volume |

Existing `v2` exports must be regenerated. The broker accepts `v3` only; do not mix schemes or silently reinterpret existing exports. The hospital's pre-existing majority/tie consolidation behaviour remains unchanged.

## Matching behaviour

| Token group | Weights in token order | Maximum |
|---|---|---:|
| 1–4 | 4, 2, 4, 4 | 4 |
| 5–7 | 5, 2, 5 | 5 |
| 8–11 | 3, 3, 3, 1 | 3 |
| 12–14 | 4, 3, 4 | 4 |
| 15–18 | 2, 1, 2, 2 | 2 |
| 19–20 | 6, 6 | 6 |

Each group contributes only its strongest matching rule. Scores range from 0 to 24. Blank tokens never match. Compatibility requires equal token scheme, key ID and method. Score 12 or higher from at least two groups proposes a match; 6–11 requires review. These provisional scores are not probabilities, and different groups still share underlying identifiers.

A new hospital record joins a global patient only if it meets the threshold against every existing member. Multiple plausible groups, inconsistent evidence or a second local ID from one hospital are held for review. Below-review evidence creates a separate global patient. An all-blank record gets a global ID marked insufficient evidence. Existing global patients are never merged automatically.

An already linked hospital/local ID retains its global ID. Consistent updates become the matching version. Conflicting updates are saved and flagged while the prior consistent version remains active for matching. Reassessment supersedes the prior review case but does not imply a human approval. No manual resolution controls are provided.

Times are stored as UTC and shown in the browser's timezone. Latest receipt and latest content change are separate. A retry does not refresh receipt time; a new submission does. Receipt time is not a clinical update date. Hospital result pages show the immutable result of that submission; the broker patient screen shows the current membership state.

## APIs and isolation

Hospital endpoints require a bearer service credential. Upload uses multipart field `file` and header `Idempotency-Key`. Duplicate identifiers with different bytes return 409. Replayed requests return the original submission. A failed matching transaction can be requeued by resending the same file and identifier.

- `POST /api/v1/submissions`
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

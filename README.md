# Hospital PPRL and PostgreSQL matching broker

The original application is retained: local hospital CSV processing, record-wise JSON delivery, the 14-token
v4 scheme, PostgreSQL matching, global patient IDs and the React broker-dashboard.
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

## Review consolidation conflicts locally

Upload a hospital CSV in the hospital workspace as usual. For each hospital/local
patient ID and linkage field, the unique most-frequent nonblank cleaned value is
automatically chosen. Only a tie for the highest count pauses the file at **Review
required**, before combinations or tokens are created. Select **Review patient
details** to see tied fields, their observed values and occurrence counts. Choose
one observed value or explicitly leave the field blank, then select **Confirm and
generate tokens**. Files without tied fields finish automatically.

For example, `ram: 3, raymon: 1` automatically selects `ram`; `ram: 2, raymon: 2`
requires a decision. A unique maximum does not need more than half of the rows:
`ram: 2, raymon: 1, ramon: 1` also selects `ram`. Counts are calculated after
cleaning and the existing exact-duplicate removal, ignoring blanks.

To select blanks for the whole review, use **Leave all review fields blank**.
This replaces choices for all review patients and pages, including pages not yet
opened. It affects only fields in this file's review list; automatically chosen
values and other patient fields stay intact. You can change individual choices
afterward. Select **Confirm and generate tokens** to save the decisions and
continue. Each blank skips combinations requiring that field. The bulk button
itself does not save decisions or generate tokens; a failed page request keeps
your previous choices.

All tied fields shown for a new file need a decision. A single nonblank value mixed
with blanks is not a conflict. Blank decisions omit every combination that needs
that field. Counts do not establish the correct identity: consult the local
source records when deciding. This first version permits observed cleaned values
and explicit blanks; to correct a value that does not appear in the choices,
correct the source CSV and upload it as a new job.

The cleaned visit rows remain intact. Confirmed choices produce one linkage row
and one token vector per local patient. Local conflict and decision JSON files
are kept under the job's runtime directory; they are not sent to the broker.
Decisions are saved when Confirm is selected, not while merely changing dropdowns.
Waiting jobs survive backend restarts, and failed token generation can be retried
from the review dialog with the saved choices. Completed files in a mixed batch
remain downloadable while another file is waiting.

Existing completed jobs and previously prepared review lists are not changed.
Older waiting jobs can still be completed using their original choices; upload
the source CSV as a new job to apply the reduced, ties-only review list. Rebuild
with `docker compose up --build -d`; no database
migration or volume reset is needed. Token definitions, scoring and broker
matching are unchanged. Token 8 remains **last name + DOB + postcode** and token 9
remains **first name + DOB + postcode**. A combination remains blank when any
required field is missing. New tokens must be explicitly sent to the broker and
are subject to its existing update and review rules.

The command-line pipeline also stops for conflicts (exit code 2) and writes a
local conflict JSON report. To use the review dialog, upload the original CSV in
the hospital workspace as a new job; the dialog cannot import a CLI output directory.
See [CHANGES_CONSOLIDATION_REVIEW.md](CHANGES_CONSOLIDATION_REVIEW.md) for the file list.

## Verify

```powershell
python -m pytest -q
python scripts/check_secure_broker.py
```

Database integration tests require a separate disposable `pprl_test*` database.
They skip when `BROKER_TEST_DATABASE_URL` is not set. Stop with
`docker compose down`; do not add `--volumes` unless deliberately deleting data.

For the 14-token v4 definitions, upgrade limitations and reproducible evaluation, see [broker/README.md](broker/README.md#v4-combinations-and-scoring). Set `PPRL_TOKEN_VERSION=v4` in existing hospital configuration before rebuilding. Preserve credentials and certificates.

# Hospital PPRL upload application

A hospital-local application for uploading CSV files, cleaning and consolidating patients, generating the existing 20 identifier combinations, and creating HMAC-SHA-256 tokens. The frontend shows the outcome for each file and provides token previews and downloads. Optional broker delivery sends individual JSON records containing hospitalID, patientID and token over TLS 1.3 with mutual certificate authentication. No CSV is transferred between hospital and broker.

**Four-laptop deployment:** see [the secure broker deployment guide](broker/README.md) for certificate generation, LAN configuration, hospital setup, operator login and end-to-end TLS tests. The broker runs independently using `compose.broker.yaml`; hospitals enable delivery using `compose.hospital.yaml`.

## Start with one command

Install Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin (Linux). Open a terminal in the extracted `PPRL_Hospital_App` folder:

```bash
docker compose up --build
```

Open **http://localhost:8080**. No separate Python, Node or Nginx installation is needed on your computer. The archive includes `.env` with the shared prototype configuration, so no setup step is required before this demonstration.

Stop with Ctrl+C, or run `docker compose down` from another terminal. Jobs and intermediate CSVs are stored in the project folder `backend/runtime/` through a Docker bind mount and survive container restarts and `docker compose down`. The project binds to localhost for this unauthenticated local prototype. Docker needs internet access for the first image build and dependency downloads.

## Use the application

1. Select **Browse files** or drag one or more CSVs onto the upload area.
2. Review the selected files; remove individual files or clear the selection as needed.
3. Select **Send to broker**. Upload progress and background preparation status appear automatically. Cleaning and tokenisation run locally; configured deployments then send individual hospital ID / local patient ID / protected token records over mTLS.
4. Review each file's **Received by broker**, **Delivery failed**, or processing failure status. Without broker configuration, files show **Ready for broker**. One failed file does not prevent other files from completing.
5. Select **Inspect prepared records** to inspect full token values in a paginated table. Scroll horizontally to see all 20 token columns.
6. Select **Download CSV** for an individual file, or **Download prepared files** for a ZIP of successful token outputs. Token reports and cleaning reports are also available.

The latest job is restored after refreshing the same browser tab. Job IDs are kept in session storage; no secret keys or patient data are stored there. Browser downloads save files to your normal download location.

## Input CSVs

- CSVs have headers. Headerless support remains commented out.
- Maximum 20 files per upload, with a 10 MiB (10 × 1024 × 1024 bytes) limit per file.
- Filenames use letters, digits, spaces, dots, underscores or hyphens and end in `.csv`.
- Each CSV contains data for one hospital. Include `hospital_id`, or use a filename containing its hospital number, such as `hospital1.csv`. The existing cleaner infers a missing hospital ID from the filename's first number.
- Required normalised columns: `local_patient_id`, `first_name`, `middle_name`, `last_name`, `email`, `date_of_birth`, `sex`, `postcode`, `phone`, `fake_medicare_id`. `hospital_id` is required unless inferred as described above.
- The existing column aliases remain supported, including `patient_id`, `dob` and `medicare`.
- Extra clinical/visit columns remain in the local cleaned CSV. They do not become token columns.

`examples/hospital1.csv` is a synthetic example, also downloadable from the interface. The original three synthetic hospital files and dataset generator are preserved under `backend/dataset/`. Uploading all three hospitals on this page simulates separate hospital nodes; each deployed hospital would normally upload only its own data.

Medicare validation follows the latest requested rule: remove spaces, slashes and hyphens, then accept exactly **10 ASCII digits**. There is no checksum or first-digit validation. This is the card number without the individual reference number. Blank values remain blank; invalid nonblank values become blank and are counted in the report. Invalid values do not necessarily fail an entire file. Patient rows with no usable combinations are clearly flagged in the results.

## Processing architecture

The frontend is plain HTML, CSS and JavaScript served by Nginx. Nginx forwards `/api/` requests to the FastAPI backend on the private Compose network.

The API invokes `pipeline.process_file()`, which calls:

```text
cleaning.process_hospital()
  → combining.create_combination_csv()
  → token_generator.tokenise_file()
```

The modules are separate Python files connected through normal Python imports and local generated CSVs. There are no subprocess calls in the pipeline. Patient consolidation is still inside cleaning.py. Detailed consolidation report output remains commented, as requested. combining.py and all 20 combination rules are unchanged.

Each job has an unpredictable UUID. Every uploaded file gets its own folder even when multiple files have the same name. Uploaded raw CSVs, cleaned data, patient linkage data and identifier combinations remain on the local backend. With broker delivery enabled, each token position is sent in its own mTLS request as a JSON object containing `hospitalID`, `patientID` and `token`, using the existing `local_patient_id`. All 20 positions per patient are included; blank combinations are sent with an empty token value. The original filename and retry information are separate metadata. Names and other raw identifying fields are excluded. The broker acknowledges the submission; failed deliveries can be retried without regenerating tokens.

The API accepts a job quickly, then processes it in a background thread. At most two jobs process concurrently; remaining jobs wait. The browser polls the saved status. If the backend restarts during processing, unfinished files are marked as interrupted and can be uploaded again. Previously completed outputs remain accessible.

## Shared key and token metadata

The root `.env` contains:

```dotenv
PPRL_TOKEN_KEY=<shared prototype secret, encoded as hexadecimal>
PPRL_KEY_ID=pprl-demo-2026-01
PPRL_TOKEN_VERSION=v2
```

Compose injects these values into the backend only. The secret is excluded from container build contents, frontend code, API responses, token CSVs and token reports. Direct Python execution reads the same root `.env`; process environment variables take precedence.

Securely provision the same key, key ID and version to participating hospital backends so identical combinations produce comparable tokens. The included secret is for this prototype and should be replaced before using real patient data. `.env` is excluded from Git, and `.env.example` is a non-secret template.

A token CSV contains:

```text
site_id, local_patient_id, token_version, key_id, method, <20 token columns>
```

The secret key is never a CSV column. `key_id` is only its non-secret label. Existing blank-combination handling remains unchanged.

The hospital UI/API remains an unauthenticated, loopback-only local prototype. The separate broker uses TLS 1.3, mTLS certificate allowlisting for hospital submissions, and HTTPS operator authentication for the dashboard. The operator dashboard shows received hospital IDs, patient IDs and full token values, with Hospital 1/2/3 filtering, pagination and automatic refresh. No broker matching is implemented; `process_received_records()` is a placeholder for PSI and the remaining broker functionality. Cleaning reports can contain local patient IDs and remain sensitive local outputs. See [broker security and deployment instructions](broker/README.md).

## Project structure

```text
PPRL_Hospital_App/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py
│   ├── src/
│   │   └── hospital/
│   │       ├── cleaning.py
│   │       ├── combining.py
│   │       ├── pipeline.py
│   │       ├── token_generator.py
│   │       └── test_token_generator.py
│   ├── dataset/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   ├── nginx.conf
│   └── Dockerfile
├── examples/hospital1.csv
├── compose.yaml
├── requirements.txt
├── .env
├── .env.example
├── .dockerignore
├── .gitignore
└── README.md
```

Runtime output structure inside the backend (bind-mounted to `backend/runtime/` in the project):

```text
/app/runtime/<job-id>/
├── job.json
├── all_tokens.zip             (created when downloaded)
└── files/<file-id>/
    ├── input/<uploaded-name>.csv
    └── output/
        ├── cleaned/
        ├── linkage_patients/
        ├── cleaning_reports/
        ├── combinations/
        ├── tokens/
        └── token_reports/
```

Outputs remain in `backend/runtime/` until those host files are deliberately deleted. Broker records and receipt metadata are stored in one CSV per hospital: `broker/data/received/hospital1.csv`, `hospital2.csv` and `hospital3.csv`. No database is used. The flow is raw CSV -> cleaned CSV -> combinations CSV -> token CSV -> individual JSON record requests with hospital/patient IDs -> broker token CSV storage. CSV files remain local to each application; further broker processing is TODO.

If upgrading from the earlier named-volume setup, existing volume data is not automatically migrated or deleted. Before recreating a running hospital container, copy its old files with `docker compose cp backend:/app/runtime/. ./backend/runtime`. On Linux hosts, give UID 10001 write access to the bind-mounted runtime folder; Windows Docker Desktop uses Windows file permissions.

## API

| Method | Route | Behaviour |
| --- | --- | --- |
| GET | `/api/health` | Backend readiness and non-secret configuration metadata. |
| POST | `/api/jobs` | Multipart upload using repeated `files` fields; returns HTTP 202 and a job ID. |
| POST | `/api/jobs/{job_id}/files/{file_id}/send` | Retry delivery of saved protected tokens; stable submission ID. |
| GET | `/api/jobs/{job_id}` | Saved batch and per-file status, metrics and output links. |
| GET | `/api/jobs/{job_id}/files/{file_id}/preview?offset=0&limit=20` | Token columns and a page of rows; maximum page size 100. |
| GET | `/api/jobs/{job_id}/files/{file_id}/download/tokens` | Individual token CSV. |
| GET | `/api/jobs/{job_id}/files/{file_id}/download/token_report` | Token report JSON. |
| GET | `/api/jobs/{job_id}/files/{file_id}/download/cleaning_report` | Cleaning report JSON. |
| GET | `/api/jobs/{job_id}/download` | ZIP of successful token files. |

HTTP 202 acknowledges the upload, not successful tokenisation. The saved status becomes `success`, `partial_success` or `failure` when processing finishes. Download routes expose only explicitly listed generated artifacts; there is no raw-file or arbitrary-path download endpoint.

## Run the CLI without Docker

From the project root, using Python 3.12:

```bash
pip install -r requirements.txt
python backend/src/hospital/pipeline.py
```

To rerun and replace previously generated CLI outputs:

```bash
python backend/src/hospital/pipeline.py --overwrite
```

CLI output goes to `backend/src/data/`. Web jobs use separate per-job folders. Direct backend development is also possible:

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The browser interface uses the Nginx proxy in Compose; opening index.html as a local file does not connect it to the API.

## File-by-file changes

| File | Change |
| --- | --- |
| `backend/app/main.py` | New upload API, background processing, saved statuses, per-file errors, previews, controlled downloads and batch ZIPs. |
| `backend/src/hospital/pipeline.py` | Refactored into a reusable `process_file()` function and retained the CLI entry point. |
| `backend/src/hospital/cleaning.py` | Applied 10-digit Medicare validation without checksum; preserved disabled headerless support and commented consolidation reporting. |
| `backend/src/hospital/token_generator.py` | Updated .env location for backend/src layout; retained shared key/version metadata and HMAC logic. |
| `backend/src/hospital/combining.py` | Moved into backend/src; contents and all rules unchanged. |
| Python `__init__.py` files | Added package markers for API imports. |
| `frontend/index.html` | New responsive upload/results interface and preview dialog. |
| `frontend/app.js` | File selection, drag-and-drop, validation, upload progress, polling, result rendering, previews and downloads. |
| `frontend/styles.css` | Responsive visual styling, status cards and preview table. |
| `frontend/nginx.conf` | Static file serving, API reverse proxy, request limit and response headers. |
| Dockerfiles and `compose.yaml` | Frontend/backend images, private backend, startup health check, localhost port and project-directory CSV/job storage. |
| Requirements files | Added pinned runtime dependencies; root file forwards to backend requirements. |
| `.dockerignore` / `.gitignore` | Exclude secrets from builds, and runtime outputs/caches from Git. |
| `.env` / `.env.example` | Preserved shared prototype configuration and template. |
| `examples/hospital1.csv` | Added a copy of the original synthetic example for frontend download. |
| `backend/dataset/` and existing test file | Moved into backend; original content retained. No new test files added. |
| `README.md` | Added setup, usage, API, architecture and verification notes. |

## Verification

Completed:

- Existing 16 token-generator tests.
- Complete CLI pipeline for all three supplied hospitals (61, 65 and 63 patient rows).
- API health, three-file multipart upload, status, preview pagination, individual downloads and batch ZIP.
- Partial and total failures, malformed headers, missing jobs/files, duplicate filenames, file count/size limits and concurrent uploads.
- Persistent job reads and restart recovery for interrupted work.
- 10-digit Medicare checks, token metadata and secret exclusion from responses and outputs.
- Python and JavaScript syntax, frontend element references, Compose YAML parsing and ZIP integrity.
- Real HTTP upload/poll/preview/download against a directly running Uvicorn backend.

Docker and browser automation were unavailable in the build workspace. Container image builds, Nginx runtime and visual browser behaviour have not been executed here. The backend was run directly with the listed runtime dependencies; the Dockerfiles and Compose configuration were checked at source level.

## Troubleshooting

- **Port 8080 is in use:** stop the other application or change the host port in compose.yaml, for example `127.0.0.1:8082:80`, then open http://localhost:8082.
- **Backend unavailable:** inspect `docker compose logs backend`. Invalid or missing key configuration prevents backend startup.
- **Missing columns:** use headers or the existing supported aliases; compare your file with the downloadable example.
- **No hospital_id:** include that column or use a filename containing the hospital number.
- **Incomplete combinations:** missing/invalid identifiers create blank tokens. Review the cleaning and token reports.
- **Modified shared configuration:** use `docker compose up --build --force-recreate` to recreate containers with the updated environment.

# Hospital-local consolidation review

This update automatically chooses a unique most-frequent nonblank cleaned value, and pauses only fields tied for the highest count before combination generation and tokenisation. It does not change broker matching, scoring or token definitions.

## Changes since the previous ZIP

This revision changes `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`, `tests/consolidation_review_dom.js`, `README.md` and this guide. The backend, token definitions and scoring retain the previous automatic-maximum implementation.

**Leave all review fields blank** sets every field in the current file's review list to blank across every page, replacing any earlier selections. It does not clear automatically chosen values or other fields outside the review list. All page requests must succeed before choices are replaced. The operator can edit individual choices afterward and then select **Confirm and generate tokens**. Bulk selection alone does not save decisions or generate tokens. For jobs prepared under the original all-conflicts review policy, this applies to that job's original review list.

The previous unique-maximum rule is retained:

| Observed nonblank values | Result |
| --- | --- |
| ram: 3, raymon: 1 | Automatically use ram |
| ram: 2, raymon: 1, ramon: 1 | Automatically use ram (unique maximum) |
| ram: 2, raymon: 2 | Review first name |
| ram: 2, raymon: 2, ramon: 1 | Review first name (highest counts tied) |
| ram: 1, blank: 3 | Automatically use ram (blanks ignored) |

The rule applies separately to each field of each hospital/local patient ID after cleaning and existing exact-duplicate removal. A patient with an automatically resolved first name and tied middle names needs a decision only for the middle name. Occurrence frequency does not independently verify which value is correct.

Existing completed jobs are unchanged. Existing waiting jobs keep their original review lists and can still be completed; upload the original CSV as a new job to use the smaller ties-only review list.

## Complete changed-file list versus original uploaded project

All paths are relative to the PPRL project root.

| File | Status | Change |
| --- | --- | --- |
| `backend/src/hospital/cleaning.py` | Modified | Automatically use unique maximum values; collect tied fields and counts; validate choices; report resolved fields and explicit blanks accurately. |
| `backend/src/hospital/pipeline.py` | Modified | Separate preparation from token generation; pause only tied fields; resume with validated choices and preserve original cleaned visits. |
| `backend/app/main.py` | Modified | Persist review state; add conflict/review endpoints; guard token downloads and broker submission until resolution; handle restart/retry and batch ZIP refresh. |
| `frontend/index.html` | Modified | Add the hospital-local patient review dialog. |
| `frontend/app.js` | Modified | Display review status; load paginated conflicts; collect choices; submit decisions and resume token generation. |
| `frontend/styles.css` | Modified | Style review fields, status, pagination and mobile layout. |
| `README.md` | Modified | Document rebuilding, review steps, saved decisions, CLI limitations and unchanged token rules. |
| `backend/tests/test_consolidation_review.py` | New | Backend and API regression tests for conflicts, decisions, guards, restart, retry, mixed batches and token order. |
| `tests/consolidation_review_dom.js` | New | UI handler tests for explicit decisions, blanks, pagination, failure guards and saved choices. |
| `CHANGES_CONSOLIDATION_REVIEW.md` | New | This change list, usage notes and verification record. |

## How to use

1. Apply the modified and new files above to your current project, preserving your existing local configuration and runtime/database data.
2. From the project root rebuild the current Compose stack: `docker compose up --build -d`.
3. Open your existing hospital workspace (default `http://localhost:8082`) and upload the original hospital CSV as a new job.
4. A file with tied highest counts shows **Review required**. Select **Review patient details**.
5. For each field select an observed cleaned value or **Leave blank**. Check the local source records before selecting; frequency alone does not prove correctness. Unique highest counts are accepted automatically for new uploads; only tied fields are shown for manual decisions.
   To set blanks for all reviewed patients and pages at once, select **Leave all review fields blank**. You can edit individual choices before confirming. Blanking a field omits combinations that require it; the source visit data is preserved.
6. Select **Confirm and generate tokens** after every displayed field in the file has a choice. Then preview or download the tokens and use the existing **Send to broker** action when ready.

For example, Hospital 2 / patient 38 may have `ram` and `raymon` twice each after cleaning. Both alternatives appear with their counts; no first-name choice is assumed. If you verify `ram` locally and select it, the confirmed linkage row uses `ram`. Any middle-name conflict needs its own decision. All distinct cleaned visit rows remain in the original cleaned file, with the existing exact-duplicate removal behavior preserved.

A single nonblank value alongside blank values is not a conflict. Only linkage fields with a tied highest count pause new files. A unique most-frequent nonblank value is used automatically, even if it represents fewer than half of the visit rows. This version requires choosing an observed value or explicit blank; correct the source CSV and upload a new job if the correct value is not listed. It still produces only one canonical patient row and one token vector per hospital/local patient ID, rather than retaining multiple token aliases.

Review data and confirmed decisions are stored locally in the job directory under `backend/runtime` (or `PPRL_RUNTIME_DIR` when configured). The initial output includes `cleaning_reports/<filename>_consolidation_conflicts.json`. Confirmed output is written to a new `output_resolved_<id>` directory with a `cleaning_reports/consolidation_resolutions.json` audit file. Local alternatives and selected identifiers are not included in broker token submissions. Choices are saved when Confirm is selected; unsaved dropdown changes are discarded when the dialog closes or reloads. Failed or interrupted finalisation retains saved choices for retry. Existing successful jobs are not retroactively changed.

The CLI pipeline also pauses with exit code 2 and writes a local conflict report. The web interface cannot import that CLI output directory: upload the original CSV as a new web job to use the dialog.

## Separate combining.py check

`backend/src/hospital/combining.py` is unchanged. Its existing rule is:

```python
combinations[column] = SEPARATOR.join(parts) if all(parts) else ""
```

It creates a combination only when every required component is nonblank. Missing any component leaves that combination blank; the existing token generator skips blank combinations. Explicitly blanking a conflict therefore removes the combinations requiring that field.

| Token | Preserved combination |
| --- | --- |
| token8 | last_name + DOB + postcode |
| token9 | first_name + DOB + postcode |

All 14 token definitions, the HMAC/version/key configuration, scoring.py, evaluation/pair-results scripts, broker code, dashboard, certificates, proxy configuration and dependencies are unchanged. There is no database migration or volume reset. Newly uploaded/confirmed tokens still follow the broker's existing update and review rules; resolving a local conflict does not guarantee a cross-hospital match or automatically reassign an existing global patient ID.

## Verification

- Supplied dataset review counts:

  | Hospital | Previous patients / fields | Ties-only patients / fields |
  | --- | --- | --- |
  | 1 | 23 / 49 | 18 / 33 |
  | 2 | 30 / 61 | 26 / 46 |
  | 3 | 25 / 58 | 21 / 44 |

- Python suite: **60 passed, 17 skipped**. Database integration tests require a separate `BROKER_TEST_DATABASE_URL` and were not run. One dependency deprecation warning was emitted.
- `node --check frontend/app.js`: passed.
- `node tests/consolidation_review_dom.js`: passed, including pagination, explicit blank, required complete decisions, load-failure guard, confirmation payload and restored saved choices. New tests cover bulk blanking across 105 patients (multiple API pages), keeping choices on a later-page failure, overriding individual blanks and requiring a separate confirmation before submission.
- Backend tests verify that all cleaned visits remain, unique maxima run automatically, only tied fields need decisions, old waiting jobs remain resumable, no tokens exist before required review, invalid/incomplete/stale decisions are rejected, restart/retry works, and batch ZIP downloads include newly completed files.
- Final code review corrected report metrics after manual confirmation and clarified CLI instructions.
- Full browser rendering was not verified: Chromium could not start in this execution environment. No Docker deployment or live broker/database test was performed.

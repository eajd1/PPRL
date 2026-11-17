# Fourteen token v4 implementation

The supplied Recommended PPRL Tokens and Scores document defines 14 numbered tokens despite its opening reference to 12. The implementation follows all 14 table rows and their worked examples.

The exact definitions, caps and upgrade steps are in [the broker guide](../broker/README.md#v4-combinations-and-scoring). This change preserves the HTTPS proxy, client-certificate identity checks, JSON record transport, PostgreSQL queue and the read-only React dashboard.

New CSV exports have 19 columns, including the existing five metadata/identity columns. Only v4 submissions are newly accepted. Historical v3 data is never reinterpreted, and old accepted queues retain their scorer. Existing multi-hospital groups may need review during version transition; this release does not silently approve a migration or merge global IDs.

## Synthetic evaluation

The reproducible comparison is [v4-synthetic-evaluation.json](v4-synthetic-evaluation.json). The repository dataset has 182 hospital records and 102 true cross-hospital pairs. V3 found 12 true pairs, missed 90, had zero false positives, and held 14.84% of records for review. V4 found 95 true pairs, missed 7, had zero false positives, and held 1.10% for review. Precision was 100% for both; recall was 11.76% versus 93.14%; F1 was 21.05% versus 96.45%.

Three additional fixed seeds each contain 120 records with deliberately shared family identifiers, DOB and household phone numbers. V4 automatically matched no pairs in those stress cohorts and held 66.67% for review. This is a material limitation: stronger shared evidence can create multiple plausible candidates and suppress automatic linkage. Zero false positives in these samples is not evidence of clinical safety. Do not present the repository-data result as a general improvement.

No threshold tuning was performed on these test cohorts. The original source weights and 12/6 thresholds remain provisional. Insurer issuer data is absent, so issuer-qualified private IDs were not invented. Use separate development datasets if tuning later, and keep these seeds as verification cases.

## Verification

Run the Python suite with a dedicated PostgreSQL 17 pprl_test database. Run the comparison script against two empty pprl_eval databases; it refuses nonempty databases. Verify the whole hospital upload, v4 preview, mTLS delivery and HTTPS dashboard using the deployment instructions. Keep existing keys, credentials, certificates and volumes. Change PPRL_TOKEN_VERSION to v4 and regenerate from original CSVs; do not relabel existing token files.

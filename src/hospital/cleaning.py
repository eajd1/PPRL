#!/usr/bin/env python3
"""Clean all hospital CSVs before local PPRL token generation.

Expected location:
    PPRL/src/hospital/cleaning.py

Default input:
    PPRL/dataset/hospital*.csv

Important:
    Cleaned CSVs still contain identifying patient data. They must remain
    inside the hospital and must never be sent to the broker.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "dataset"


# ---------------------------------------------------------------------------
# Dataset schema
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "local_patient_id",
    "first_name",
    "last_name",
    "date_of_birth",
    "sex",
    "postcode",
    "phone",
    "fake_medicare_id",
    "diagnosis_code",
    "visit_date",
    "hospital_id",
]


# Current dataset_generator.py output order when the CSV has no header.
HEADERLESS_COLUMN_ORDER = [
    "first_name",
    "last_name",
    "date_of_birth",
    "local_patient_id",
    "sex",
    "postcode",
    "phone",
    "fake_medicare_id",
    "diagnosis_code",
    "visit_date",
    "hospital_id",
]


# Only these fields are used for patient linkage.
LINKAGE_COLUMNS = [
    "first_name",
    "last_name",
    "date_of_birth",
    "sex",
    "postcode",
    "phone",
    "fake_medicare_id",
]


SEX_MAP = {
    "m": "M",
    "male": "M",
    "f": "F",
    "female": "F",
    "o": "O",
    "other": "O",
}


# Australian day-first formats are checked before ISO formats.
DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
)


class CleaningError(ValueError):
    """Raised when a hospital CSV cannot be cleaned safely."""


# ---------------------------------------------------------------------------
# Reading and schema validation
# ---------------------------------------------------------------------------

def normalise_header(value: str) -> str:
    """Convert a header to lowercase snake_case."""

    return re.sub(
        r"[\s-]+",
        "_",
        value.strip().lower(),
    )


def read_hospital_csv(
    path: Path,
) -> tuple[pd.DataFrame, bool]:
    """Read a headered or agreed headerless hospital CSV.

    Returns:
        DataFrame containing the hospital data.
        Boolean showing whether the input contained a header.
    """

    try:
        first_row = pd.read_csv(
            path,
            header=None,
            nrows=1,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )

    except pd.errors.EmptyDataError as exc:
        raise CleaningError("CSV is empty") from exc

    except (
        OSError,
        pd.errors.ParserError,
        UnicodeDecodeError,
    ) as exc:
        raise CleaningError(
            f"CSV could not be read: {exc}"
        ) from exc

    if first_row.empty:
        raise CleaningError("CSV is empty")

    possible_headers = [
        normalise_header(str(value))
        for value in first_row.iloc[0]
    ]

    required = set(REQUIRED_COLUMNS)
    recognised = required.intersection(possible_headers)

    if required.issubset(possible_headers):
        # The CSV has a complete header row.
        frame = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )

        frame.columns = [
            normalise_header(str(column))
            for column in frame.columns
        ]

        had_header = True

    elif recognised:
        # Some headings exist, but the header is incomplete.
        missing_columns = sorted(
            required.difference(possible_headers)
        )

        raise CleaningError(
            "CSV appears to have a header, but required "
            "columns are missing: "
            + ", ".join(missing_columns)
        )

    else:
        # The current generated datasets are headerless.
        if first_row.shape[1] != len(
            HEADERLESS_COLUMN_ORDER
        ):
            raise CleaningError(
                "Headerless CSV must have exactly "
                f"{len(HEADERLESS_COLUMN_ORDER)} columns; "
                f"found {first_row.shape[1]}"
            )

        frame = pd.read_csv(
            path,
            header=None,
            names=HEADERLESS_COLUMN_ORDER,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )

        had_header = False

    duplicate_headers = frame.columns[
        frame.columns.duplicated()
    ].tolist()

    if duplicate_headers:
        raise CleaningError(
            "Duplicate column names: "
            + ", ".join(duplicate_headers)
        )

    missing_columns = [
        column
        for column in REQUIRED_COLUMNS
        if column not in frame.columns
    ]

    if missing_columns:
        raise CleaningError(
            "Missing required columns: "
            + ", ".join(missing_columns)
        )

    # Preserve leading zeroes in IDs, postcodes and phone numbers.
    frame = frame.fillna("").astype(str)

    return frame, had_header


# ---------------------------------------------------------------------------
# Individual field cleaning
# ---------------------------------------------------------------------------

def clean_name(
    value: str,
) -> tuple[str, bool]:
    """Clean a patient name.

    Rules:
        - Trim spaces.
        - Convert to lowercase.
        - Normalise Unicode.
        - Remove accents.
        - Remove punctuation and spaces.
        - Reject names containing numbers.

    Returns:
        cleaned value
        invalid-value flag
    """

    raw = value.strip()

    if not raw:
        return "", False

    text = unicodedata.normalize(
        "NFKD",
        raw.casefold(),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )

    # A number in a patient name is treated as invalid.
    if any(character.isdigit() for character in text):
        return "", True

    cleaned = "".join(
        character
        for character in text
        if character.isalpha()
    )

    return cleaned, not bool(cleaned)


def clean_date_of_birth(
    value: str,
) -> tuple[str, bool]:
    """Convert a valid DOB to YYYY-MM-DD."""

    raw = value.strip()

    if not raw:
        return "", False

    for date_format in DATE_FORMATS:
        try:
            cleaned = datetime.strptime(
                raw,
                date_format,
            ).date().isoformat()

            return cleaned, False

        except ValueError:
            continue

    # Invalid or unsupported dates become blank.
    return "", True


def clean_sex(
    value: str,
) -> tuple[str, bool]:
    """Convert recognised sex values to M, F or O."""

    raw = value.strip()

    if not raw:
        return "", False

    cleaned = SEX_MAP.get(raw.casefold())

    if cleaned is None:
        return "", True

    return cleaned, False


def clean_postcode(
    value: str,
) -> tuple[str, bool]:
    """Return postcode as a four-character string."""

    raw = value.strip()

    if not raw:
        return "", False

    if not raw.isdigit() or len(raw) > 4:
        return "", True

    return raw.zfill(4), False


def clean_phone(value: str) -> tuple[str, bool]:
    """Standardise an Australian phone number to +61 format.

    Examples:
        0412 345 678   -> +61412345678
        +61 412 345 678 -> +61412345678
        (02) 9876 5432 -> +61298765432

    Returns:
        cleaned phone number
        invalid-value flag
    """

    raw = value.strip()

    if not raw:
        return "", False

    # Retain digits only.
    digits = re.sub(r"\D", "", raw)

    # International Australian format: 61412345678
    if digits.startswith("61"):
        national_number = digits[2:]

    # Australian national format: 0412345678
    elif digits.startswith("0"):
        national_number = digits[1:]

    else:
        return "", True

    # Australian numbers contain nine digits after +61.
    if len(national_number) != 9:
        return "", True

    cleaned = "+61" + national_number

    return cleaned, False


def clean_fake_medicare_id(
    value: str,
) -> tuple[str, bool]:
    """Remove spaces and punctuation from fake Medicare ID."""

    raw = value.strip()

    if not raw:
        return "", False

    cleaned = "".join(
        character
        for character in raw
        if character.isalnum()
    )

    return cleaned, not bool(cleaned)


# ---------------------------------------------------------------------------
# Applying cleaning rules
# ---------------------------------------------------------------------------

def apply_cleaner(
    series: pd.Series,
    cleaner: Callable[[str], tuple[str, bool]],
) -> tuple[pd.Series, int, int]:
    """Apply one cleaner and calculate report statistics."""

    cleaned_values: list[str] = []
    invalid_count = 0
    changed_count = 0

    for original in series.tolist():
        cleaned, invalid = cleaner(original)

        cleaned_values.append(cleaned)
        invalid_count += int(invalid)
        changed_count += int(cleaned != original)

    cleaned_series = pd.Series(
        cleaned_values,
        index=series.index,
    )

    return (
        cleaned_series,
        invalid_count,
        changed_count,
    )


def blank_counts(
    frame: pd.DataFrame,
) -> dict[str, int]:
    """Count blank values without exposing patient information."""

    return {
        column: int(
            frame[column]
            .str.strip()
            .eq("")
            .sum()
        )
        for column in frame.columns
    }


def validate_ids(
    frame: pd.DataFrame,
) -> str:
    """Validate local_patient_id and hospital_id."""

    blank_local_ids = int(
        frame["local_patient_id"]
        .str.strip()
        .eq("")
        .sum()
    )

    if blank_local_ids:
        raise CleaningError(
            "local_patient_id is blank in "
            f"{blank_local_ids} row(s)"
        )

    blank_hospital_ids = int(
        frame["hospital_id"]
        .str.strip()
        .eq("")
        .sum()
    )

    if blank_hospital_ids:
        raise CleaningError(
            "hospital_id is blank in "
            f"{blank_hospital_ids} row(s)"
        )

    hospital_ids = (
        frame["hospital_id"]
        .drop_duplicates()
        .tolist()
    )

    if len(hospital_ids) != 1:
        raise CleaningError(
            "One hospital CSV must contain exactly "
            "one hospital_id; "
            f"found {len(hospital_ids)}"
        )

    return hospital_ids[0]


# ---------------------------------------------------------------------------
# Quality reporting
# ---------------------------------------------------------------------------

def patient_quality_metrics(
    frame: pd.DataFrame,
) -> dict[str, object]:
    """Calculate repeated-patient and conflict statistics."""

    patient_counts = (
        frame.groupby("local_patient_id")
        .size()
    )

    # Ignore blanks when identifying contradictory values.
    linkage_values = (
        frame[LINKAGE_COLUMNS]
        .replace("", pd.NA)
    )

    variants = linkage_values.groupby(
        frame["local_patient_id"]
    ).nunique(dropna=True)

    return {
        "unique_local_patient_ids": int(
            frame["local_patient_id"].nunique()
        ),
        "additional_visit_rows_retained": int(
            frame["local_patient_id"]
            .duplicated()
            .sum()
        ),
        "local_patient_ids_with_multiple_rows": int(
            patient_counts.gt(1).sum()
        ),
        "local_patient_ids_with_conflicting_linkage_values": int(
            variants.gt(1)
            .any(axis=1)
            .sum()
        ),
        "conflicts_by_linkage_field": {
            column: int(
                variants[column]
                .gt(1)
                .sum()
            )
            for column in LINKAGE_COLUMNS
        },
    }


# ---------------------------------------------------------------------------
# Creating one patient-level linkage record
# ---------------------------------------------------------------------------

def choose_canonical_value(
    values: pd.Series,
) -> tuple[str, str]:
    """Select one value for a repeated local patient.

    Decisions:
        missing:
            Every value is blank.

        consistent:
            All nonblank values agree.

        majority:
            One value occurs more frequently than all others.

        tie:
            Two or more values have the same highest frequency.
            A tie is returned as blank rather than guessed.
    """

    nonblank_values = [
        value
        for value in values.tolist()
        if value != ""
    ]

    if not nonblank_values:
        return "", "missing"

    counts = Counter(nonblank_values)
    highest_count = max(counts.values())

    winners = [
        value
        for value, count in counts.items()
        if count == highest_count
    ]

    if len(winners) > 1:
        return "", "tie"

    selected_value = winners[0]

    if len(counts) == 1:
        return selected_value, "consistent"

    return selected_value, "majority"


def build_patient_linkage_table(
    cleaned_visits: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Create one linkage record per local patient.

    diagnosis_code and visit_date are excluded because they represent
    clinical events and must not affect identity linkage.
    """

    patient_rows: list[dict[str, str]] = []

    majority_counts = {
        column: 0
        for column in LINKAGE_COLUMNS
    }

    tie_counts = {
        column: 0
        for column in LINKAGE_COLUMNS
    }

    patients_with_conflicts: set[
        tuple[str, str]
    ] = set()

    patients_with_ties: set[
        tuple[str, str]
    ] = set()

    patient_groups = cleaned_visits.groupby(
        [
            "hospital_id",
            "local_patient_id",
        ],
        sort=False,
        dropna=False,
    )

    for (
        hospital_id,
        local_patient_id,
    ), patient_group in patient_groups:

        patient_key = (
            hospital_id,
            local_patient_id,
        )

        patient_row = {
            "hospital_id": hospital_id,
            "local_patient_id": local_patient_id,
        }

        for column in LINKAGE_COLUMNS:
            value, decision = choose_canonical_value(
                patient_group[column]
            )

            patient_row[column] = value

            if decision == "majority":
                majority_counts[column] += 1
                patients_with_conflicts.add(
                    patient_key
                )

            elif decision == "tie":
                tie_counts[column] += 1

                patients_with_conflicts.add(
                    patient_key
                )

                patients_with_ties.add(
                    patient_key
                )

        patient_rows.append(patient_row)

    output_columns = [
        "hospital_id",
        "local_patient_id",
        *LINKAGE_COLUMNS,
    ]

    linkage_table = pd.DataFrame(
        patient_rows,
        columns=output_columns,
    )

    consolidation_report = {
        "patient_linkage_rows": len(
            linkage_table
        ),
        "visit_rows_collapsed": (
            len(cleaned_visits)
            - len(linkage_table)
        ),
        "patients_with_conflicting_linkage_values": len(
            patients_with_conflicts
        ),
        "patients_with_unresolved_ties": len(
            patients_with_ties
        ),
        "fields_resolved_using_unique_majority": (
            majority_counts
        ),
        "fields_left_blank_due_to_tie": (
            tie_counts
        ),
        "consolidation_rule": (
            "Use the only nonblank value when consistent; "
            "use a unique most-frequent value when available; "
            "leave tied values blank."
        ),
    }

    return (
        linkage_table,
        consolidation_report,
    )


# ---------------------------------------------------------------------------
# Cleaning one hospital DataFrame
# ---------------------------------------------------------------------------

def clean_dataframe(
    frame: pd.DataFrame,
    source_file: str,
    had_header: bool,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Clean one hospital DataFrame and create its report."""

    input_rows = len(frame)
    missing_before = blank_counts(frame)

    # Remove completely identical rows.
    duplicate_mask = frame.duplicated(
        keep="first"
    )

    duplicates_removed = int(
        duplicate_mask.sum()
    )

    cleaned = (
        frame.loc[~duplicate_mask]
        .copy()
        .reset_index(drop=True)
    )

    hospital_id = validate_ids(cleaned)

    cleaners = {
        "first_name": clean_name,
        "last_name": clean_name,
        "date_of_birth": clean_date_of_birth,
        "sex": clean_sex,
        "postcode": clean_postcode,
        "phone": clean_phone,
        "fake_medicare_id": clean_fake_medicare_id,
    }

    invalid_counts: dict[str, int] = {}
    changed_counts: dict[str, int] = {}

    for column, cleaner in cleaners.items():
        (
            cleaned[column],
            invalid_counts[column],
            changed_counts[column],
        ) = apply_cleaner(
            cleaned[column],
            cleaner,
        )

    # Use a consistent column order.
    extra_columns = [
        column
        for column in cleaned.columns
        if column not in REQUIRED_COLUMNS
    ]

    cleaned = cleaned[
        REQUIRED_COLUMNS + extra_columns
    ]

    cleaning_report: dict[str, object] = {
        "source_file": source_file,
        "input_had_header": had_header,
        "hospital_id": hospital_id,
        "input_rows": input_rows,
        "cleaned_visit_rows": len(cleaned),
        "exact_duplicate_rows_removed": (
            duplicates_removed
        ),
        "missing_values_before_cleaning": (
            missing_before
        ),
        "missing_values_after_cleaning": (
            blank_counts(cleaned)
        ),
        "invalid_values_replaced_with_blank": (
            invalid_counts
        ),
        "values_changed_by_standardisation": (
            changed_counts
        ),
        "preserved_columns": [
            "local_patient_id",
            "hospital_id",
            "diagnosis_code",
            "visit_date",
        ],
        **patient_quality_metrics(cleaned),
        "privacy_note": (
            "The cleaned CSV contains identifying "
            "patient data and must remain local."
        ),
    }

    return cleaned, cleaning_report


# ---------------------------------------------------------------------------
# Writing files
# ---------------------------------------------------------------------------

def write_json(
    data: dict[str, object],
    path: Path,
) -> None:
    """Write a formatted JSON report."""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def process_hospital(
    input_path: Path,
    cleaned_output_dir: Path,
    linkage_output_dir: Path,
    report_dir: Path,
    overwrite: bool,
) -> dict[str, object]:
    """Clean one hospital and create all local outputs."""

    cleaned_path = (
        cleaned_output_dir
        / f"{input_path.stem}_cleaned.csv"
    )

    linkage_path = (
        linkage_output_dir
        / f"{input_path.stem}_linkage_patients.csv"
    )

    report_path = (
        report_dir
        / f"{input_path.stem}_cleaning_report.json"
    )

    if not overwrite:
        existing_outputs = [
            path
            for path in (
                cleaned_path,
                linkage_path,
                report_path,
            )
            if path.exists()
        ]

        if existing_outputs:
            raise CleaningError(
                f"Output already exists: "
                f"{existing_outputs[0]}. "
                "Use --overwrite to replace it."
            )

    frame, had_header = read_hospital_csv(
        input_path
    )

    cleaned_visits, cleaning_report = (
        clean_dataframe(
            frame,
            input_path.name,
            had_header,
        )
    )

    linkage_patients, consolidation_report = (
        build_patient_linkage_table(
            cleaned_visits
        )
    )

    cleaned_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    linkage_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    cleaned_visits.to_csv(
        cleaned_path,
        index=False,
        encoding="utf-8",
    )

    linkage_patients.to_csv(
        linkage_path,
        index=False,
        encoding="utf-8",
    )

    cleaning_report["cleaned_file"] = (
        cleaned_path.name
    )

    cleaning_report["linkage_patient_file"] = (
        linkage_path.name
    )

    cleaning_report["patient_consolidation"] = (
        consolidation_report
    )

    write_json(
        cleaning_report,
        report_path,
    )

    return {
        "source_file": input_path.name,
        "hospital_id": cleaning_report["hospital_id"],
        "status": "success",
        "input_rows": cleaning_report["input_rows"],
        "cleaned_visit_rows": (
            cleaning_report["cleaned_visit_rows"]
        ),
        "patient_linkage_rows": (
            consolidation_report[
                "patient_linkage_rows"
            ]
        ),
        "exact_duplicates_removed": (
            cleaning_report[
                "exact_duplicate_rows_removed"
            ]
        ),
        "cleaned_file": str(cleaned_path),
        "linkage_patient_file": str(
            linkage_path
        ),
        "report_file": str(report_path),
    }


# ---------------------------------------------------------------------------
# Hospital file discovery
# ---------------------------------------------------------------------------

def natural_sort_key(
    path: Path,
) -> list[object]:
    """Sort hospital2.csv before hospital10.csv."""

    return [
        int(part)
        if part.isdigit()
        else part.casefold()
        for part in re.split(
            r"(\d+)",
            path.name,
        )
    ]


def discover_hospitals(
    input_dir: Path,
    pattern: str,
) -> list[Path]:
    """Find current and future hospital CSV files."""

    if not input_dir.is_dir():
        raise CleaningError(
            f"Dataset directory does not exist: "
            f"{input_dir}"
        )

    hospital_files = [
        path
        for path in input_dir.glob(pattern)
        if path.is_file()
        and not path.stem.endswith("_cleaned")
    ]

    hospital_files.sort(
        key=natural_sort_key
    )

    if not hospital_files:
        raise CleaningError(
            f"No files matching {pattern!r} "
            f"were found in {input_dir}"
        )

    return hospital_files


# ---------------------------------------------------------------------------
# Command-line execution
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean and consolidate all hospital CSV files"
        )
    )

    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DATASET_DIR,
    )

    parser.add_argument(
        "--pattern",
        default="hospital*.csv",
    )

    parser.add_argument(
        "--cleaned-output-dir",
        type=Path,
        default=DATASET_DIR / "cleaned",
    )

    parser.add_argument(
        "--linkage-output-dir",
        type=Path,
        default=DATASET_DIR / "linkage_patients",
    )

    parser.add_argument(
        "--report-dir",
        type=Path,
        default=DATASET_DIR / "cleaning_reports",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace existing cleaned files "
            "and reports"
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    try:
        hospital_files = discover_hospitals(
            args.input_dir,
            args.pattern,
        )

        batch_report_path = (
            args.report_dir
            / "batch_cleaning_report.json"
        )

        if (
            batch_report_path.exists()
            and not args.overwrite
        ):
            raise CleaningError(
                f"Output already exists: "
                f"{batch_report_path}. "
                "Use --overwrite to run again."
            )

    except CleaningError as exc:
        print(
            f"Cleaning failed: {exc}",
            file=sys.stderr,
        )

        return 1

    started = datetime.now(timezone.utc)

    successful_files: list[
        dict[str, object]
    ] = []

    failed_files: list[
        dict[str, str]
    ] = []

    for hospital_file in hospital_files:
        try:
            result = process_hospital(
                hospital_file,
                args.cleaned_output_dir,
                args.linkage_output_dir,
                args.report_dir,
                args.overwrite,
            )

            successful_files.append(result)

            print(
                f"[OK] {hospital_file.name}: "
                f"{result['input_rows']} input rows, "
                f"{result['cleaned_visit_rows']} "
                f"cleaned visit rows, "
                f"{result['patient_linkage_rows']} "
                f"patient linkage rows"
            )

        except (
            CleaningError,
            OSError,
            pd.errors.ParserError,
        ) as exc:
            failed_files.append(
                {
                    "source_file": (
                        hospital_file.name
                    ),
                    "status": "failed",
                    "error": str(exc),
                }
            )

            print(
                f"[FAILED] "
                f"{hospital_file.name}: {exc}",
                file=sys.stderr,
            )

    completed = datetime.now(timezone.utc)

    batch_report: dict[str, object] = {
        "started_at_utc": (
            started.isoformat()
        ),
        "completed_at_utc": (
            completed.isoformat()
        ),
        "duration_seconds": round(
            (
                completed
                - started
            ).total_seconds(),
            6,
        ),
        "files_discovered": len(
            hospital_files
        ),
        "files_succeeded": len(
            successful_files
        ),
        "files_failed": len(
            failed_files
        ),
        "successful_files": (
            successful_files
        ),
        "failed_files": failed_files,
    }

    write_json(
        batch_report,
        batch_report_path,
    )

    print(
        f"Finished: "
        f"{len(successful_files)} succeeded, "
        f"{len(failed_files)} failed.\n"
        f"Batch report: "
        f"{batch_report_path}"
    )

    return 1 if failed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
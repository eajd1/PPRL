#!/usr/bin/env python3
"""Clean all hospital CSV files before local PPRL token generation.

Expected structure:

PPRL/
└── src/
    ├── data/
    │   ├── hospital1.csv
    │   ├── hospital2.csv
    │   └── hospital3.csv
    └── hospital/
        └── cleaning.py

The script automatically processes every hospital*.csv file inside src/data.
Additional files such as hospital4.csv will be discovered automatically.
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
# Paths
# ---------------------------------------------------------------------------

# cleaning.py is expected at src/hospital/cleaning.py
SRC_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = SRC_DIR / "data"


# ---------------------------------------------------------------------------
# Dataset configuration
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
    "middle_name",
    "email",
    "hospital_id",
]

# Used when a CSV does not contain a header row.
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

LINKAGE_COLUMNS = [
    "first_name",
    "middle_name",
    "last_name",
    "email",
    "date_of_birth",
    "sex",
    "postcode",
    "phone",
    "fake_medicare_id",
]

SEX_MAP = {
    "m": "M",
    "male": "M",
    "man": "M",

    "f": "F",
    "female": "F",
    "woman": "F",

    "o": "O",
    "other": "O",
    "nonbinary": "O",
    "non-binary": "O",
    "non binary": "O",

    "u": "U",
    "?": "U",
    "unknown": "U",
    "unspecified": "U",
    "not specified": "U",
    "not provided": "U",
    "n/a": "U",
    "na": "U",
    "prefer not to say": "U",
}

# Australian numeric dates are interpreted as day/month/year.
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",

    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",

    "%d %b %Y",       # 29 Jul 1944
    "%d %B %Y",       # 29 July 1944
    "%d %b, %Y",      # 29 Jul, 1944
    "%d %B, %Y",      # 29 July, 1944

    "%b %d, %Y",      # Jul 29, 1944
    "%B %d, %Y",      # July 29, 1944
    "%b %d %Y",       # Jul 29 1944
    "%B %d %Y",       # July 29 1944
)

Cleaner = Callable[[str], tuple[str, bool]]


class CleaningError(ValueError):
    """Raised when a hospital CSV cannot be cleaned safely."""


# ---------------------------------------------------------------------------
# Reading CSV files
# ---------------------------------------------------------------------------

# Other names a hospital file may use for the same column.
COLUMN_ALIASES = {
    "dob": "date_of_birth",
    "birth_date": "date_of_birth",
    "medicare": "fake_medicare_id",
    "medicare_id": "fake_medicare_id",
    "middle": "middle_name",
    "patient_id": "local_patient_id",
    "site_id": "hospital_id",
    "gender": "sex",
}


def normalise_header(value: str) -> str:
    """Convert column headings to lowercase snake_case."""

    name = re.sub(
        r"[\s-]+",
        "_",
        value.strip().lower(),
    )

    return COLUMN_ALIASES.get(name, name)

def hospital_id_from_filename(path: Path) -> str:
    """Take the hospital number from the file name: hospital1.csv -> "1"."""

    match = re.search(r"(\d+)", path.stem)

    if not match:
        raise CleaningError(
            f"No hospital_id column and no number in file name {path.name}"
        )

    return match.group(1)


def read_hospital_csv(
    path: Path,
) -> tuple[pd.DataFrame, bool]:
    """Read a hospital CSV with or without a header."""

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

    first_values = [
        normalise_header(str(value))
        for value in first_row.iloc[0]
    ]

    # hospital_id may be missing; it is then taken from the file name.
    required = set(REQUIRED_COLUMNS) - {"hospital_id"}
    recognised = required.intersection(first_values)

    # CSV contains a complete header.
    if required.issubset(first_values):
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

        if "hospital_id" not in frame.columns:
            frame["hospital_id"] = hospital_id_from_filename(path)

    # Some headings were found, but required headings are missing.
    elif recognised:
        missing = sorted(
            required.difference(first_values)
        )

        raise CleaningError(
            "CSV appears to have a header but is missing "
            "required columns: "
            + ", ".join(missing)
        )

    # Treat the file as headerless.
    else:
        expected_count = len(
            HEADERLESS_COLUMN_ORDER
        )

        if first_row.shape[1] != expected_count:
            raise CleaningError(
                "Headerless CSV must contain exactly "
                f"{expected_count} columns; "
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

    # Keep all values as strings so leading zeroes are preserved.
    return frame.fillna("").astype(str), had_header


# ---------------------------------------------------------------------------
# Field cleaners
# ---------------------------------------------------------------------------

def clean_name(value: str) -> tuple[str, bool]:
    """Lowercase a name and remove accents, spaces and punctuation.

    A nonblank name containing a number is treated as invalid.
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

    # Numbers are not expected in a person's name.
    if any(character.isdigit() for character in text):
        return "", True

    cleaned = "".join(
        character
        for character in text
        if character.isalpha()
    )

    if not cleaned:
        return "", True

    return cleaned, False


def clean_date_of_birth(
    value: str,
) -> tuple[str, bool]:
    """Convert a recognised valid DOB to YYYY-MM-DD."""

    raw = value.strip()

    if not raw:
        return "", False

    for date_format in DATE_FORMATS:
        try:
            parsed_date = datetime.strptime(
                raw,
                date_format,
            ).date()
            # Two-digit years: "29" is read as 2029. A birth date
            # cannot be in the future, so move it back 100 years.
            if parsed_date > datetime.now().date():
                parsed_date = parsed_date.replace(
                    year=parsed_date.year - 100
                )
            return parsed_date.isoformat(), False

        except ValueError:
            continue

    # Invalid or unsupported dates are left blank.
    return "", True


def clean_sex(value: str) -> tuple[str, bool]:
    """Standardise sex values to M, F, O or U.

    M = Male
    F = Female
    O = Other
    U = Unknown, missing or unrecognised
    """

    raw = value.strip()

    # Missing values become Unknown.
    if not raw:
        return "U", False

    cleaned = SEX_MAP.get(
        raw.casefold()
    )

    # Unexpected values also become Unknown,
    # but are reported as invalid/unrecognised.
    if cleaned is None:
        return "U", True

    return cleaned, False


def clean_postcode(
    value: str,
) -> tuple[str, bool]:
    """Return an Australian postcode as four digits."""

    raw = value.strip()

    if not raw:
        return "", False

    if not raw.isdigit():
        return "", True

    if len(raw) > 4:
        return "", True

    return raw.zfill(4), False


def clean_phone(value: str) -> tuple[str, bool]:
    """Standardise an Australian phone number to +61 format.

    Examples:
        0412 345 678    -> +61412345678
        +61 412 345 678 -> +61412345678
        (02) 9876 5432  -> +61298765432
    """

    raw = value.strip()

    if not raw:
        return "", False

    digits = re.sub(r"\D", "", raw)

    if digits.startswith("0061"):
        national_number = digits[4:]

    elif digits.startswith("61"):
        national_number = digits[2:]

    elif digits.startswith("0"):
        national_number = digits[1:]

    else:
        return "", True

    # Australian numbers contain nine digits after +61.
    if len(national_number) != 9:
        return "", True

    return "+61" + national_number, False


def clean_fake_medicare_id(
    value: str,
) -> tuple[str, bool]:
    """Remove spaces and punctuation from fake Medicare IDs."""

    raw = value.strip()

    if not raw:
        return "", False

    cleaned = "".join(
        character
        for character in raw
        if character.isalnum()
    )

    if not cleaned:
        return "", True

    return cleaned, False


def clean_email(value: str) -> tuple[str, bool]:
    """Lowercase an email address and check it looks like one."""

    raw = value.strip().lower()

    if not raw:
        return "", False

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
        return "", True

    return raw, False

def apply_cleaner(
    series: pd.Series,
    cleaner: Cleaner,
) -> tuple[pd.Series, int, int]:
    """Apply one cleaner and count invalid and changed values."""

    cleaned_values: list[str] = []
    invalid_count = 0
    changed_count = 0

    for original in series.astype(str).tolist():
        cleaned, invalid = cleaner(original)

        cleaned_values.append(cleaned)
        invalid_count += int(invalid)
        changed_count += int(cleaned != original)

    cleaned_series = pd.Series(
        cleaned_values,
        index=series.index,
        dtype="object",
    )

    return (
        cleaned_series,
        invalid_count,
        changed_count,
    )


# ---------------------------------------------------------------------------
# Validation and reporting helpers
# ---------------------------------------------------------------------------

def blank_counts(
    frame: pd.DataFrame,
) -> dict[str, int]:
    """Count blank values without exposing patient data."""

    return {
        column: int(
            frame[column]
            .astype(str)
            .str.strip()
            .eq("")
            .sum()
        )
        for column in frame.columns
    }


def remove_rows_without_ids(
    frame: pd.DataFrame,
    had_header: bool,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Remove rows with missing patient or hospital identifiers.

    Rejected rows are recorded using their original CSV row number.
    Identifiable linkage fields are not copied into the report.
    """

    blank_local_id = (
        frame["local_patient_id"]
        .astype(str)
        .str.strip()
        .eq("")
    )

    blank_hospital_id = (
        frame["hospital_id"]
        .astype(str)
        .str.strip()
        .eq("")
    )

    rejected_mask = (
        blank_local_id
        | blank_hospital_id
    )

    rejected_row_details: list[
        dict[str, object]
    ] = []

    for index, row in frame.loc[
        rejected_mask
    ].iterrows():

        reasons: list[str] = []

        if not str(
            row["local_patient_id"]
        ).strip():
            reasons.append(
                "blank_local_patient_id"
            )

        if not str(
            row["hospital_id"]
        ).strip():
            reasons.append(
                "blank_hospital_id"
            )

        # For a headered CSV, DataFrame index 0 is CSV row 2.
        # For a headerless CSV, index 0 is CSV row 1.
        csv_row_number = (
            int(index) + 2
            if had_header
            else int(index) + 1
        )

        rejected_row_details.append(
            {
                "csv_row_number": (
                    csv_row_number
                ),
                "reasons": reasons,
                "local_patient_id": str(
                    row["local_patient_id"]
                ),
                "hospital_id": str(
                    row["hospital_id"]
                ),
            }
        )

    rejection_report: dict[str, object] = {
        "blank_local_patient_id": int(
            blank_local_id.sum()
        ),
        "blank_hospital_id": int(
            blank_hospital_id.sum()
        ),
        "total_rows_rejected_for_missing_ids": int(
            rejected_mask.sum()
        ),
        "rows": rejected_row_details,
    }

    valid_rows = (
        frame.loc[~rejected_mask]
        .copy()
        .reset_index(drop=True)
    )

    if valid_rows.empty:
        raise CleaningError(
            "No usable rows remain after rejecting "
            "rows with blank identifiers"
        )

    return valid_rows, rejection_report


def validate_hospital_id(
    frame: pd.DataFrame,
) -> str:
    """Require one hospital_id per hospital CSV."""

    hospital_ids = (
        frame["hospital_id"]
        .drop_duplicates()
        .tolist()
    )

    if len(hospital_ids) != 1:
        raise CleaningError(
            "One hospital CSV must contain exactly "
            "one hospital_id; found "
            f"{len(hospital_ids)} distinct values"
        )

    return hospital_ids[0]


def patient_quality_metrics(
    frame: pd.DataFrame,
) -> dict[str, object]:
    """Calculate repeated-patient and linkage-conflict counts."""

    patient_counts = frame.groupby(
        ["hospital_id", "local_patient_id"],
        sort=False,
    ).size()

    patient_keys = [
        frame["hospital_id"],
        frame["local_patient_id"],
    ]

    variants = (
        frame[LINKAGE_COLUMNS]
        .replace("", pd.NA)
        .groupby(
            patient_keys,
            sort=False,
        )
        .nunique(dropna=True)
    )

    return {
        "unique_local_patient_ids": int(
            frame[
                ["hospital_id", "local_patient_id"]
            ]
            .drop_duplicates()
            .shape[0]
        ),
        "additional_visit_rows_retained": int(
            frame.duplicated(
                subset=[
                    "hospital_id",
                    "local_patient_id",
                ]
            ).sum()
        ),
        "local_patient_ids_with_multiple_rows": int(
            patient_counts.gt(1).sum()
        ),
        "local_patient_ids_with_conflicting_linkage_values": int(
            variants.gt(1).any(axis=1).sum()
        ),
        "conflicts_by_linkage_field": {
            column: int(
                variants[column].gt(1).sum()
            )
            for column in LINKAGE_COLUMNS
        },
    }


# ---------------------------------------------------------------------------
# Patient-level consolidation
# ---------------------------------------------------------------------------

def choose_patient_value(
    values: pd.Series,
) -> tuple[str, str]:
    """Select one linkage value for a patient.

    Returns:
        selected value
        resolution type: missing, consistent, majority or tie
    """

    nonblank_values = [
        str(value)
        for value in values
        if str(value).strip()
    ]

    if not nonblank_values:
        return "", "missing"

    counts = Counter(nonblank_values)

    if len(counts) == 1:
        return nonblank_values[0], "consistent"

    most_common = counts.most_common()

    if (
        len(most_common) == 1
        or most_common[0][1] > most_common[1][1]
    ):
        return most_common[0][0], "majority"

    # Do not silently choose between equally common conflicting values.
    return "", "tie"


def build_patient_linkage_table(
    cleaned_visits: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Create one linkage row per local patient.

    diagnosis_code and visit_date are deliberately excluded. Therefore,
    separate visits do not generate duplicate tokenisation records.
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

    conflicting_patients: set[
        tuple[str, str]
    ] = set()

    tied_patients: set[
        tuple[str, str]
    ] = set()

    grouped = cleaned_visits.groupby(
        ["hospital_id", "local_patient_id"],
        sort=False,
        dropna=False,
    )

    for (
        hospital_id,
        local_patient_id,
    ), group in grouped:

        patient_key = (
            str(hospital_id),
            str(local_patient_id),
        )

        patient_row = {
            "hospital_id": str(hospital_id),
            "local_patient_id": str(
                local_patient_id
            ),
        }

        for column in LINKAGE_COLUMNS:
            selected, resolution = (
                choose_patient_value(
                    group[column]
                )
            )

            patient_row[column] = selected

            if resolution in {
                "majority",
                "tie",
            }:
                conflicting_patients.add(
                    patient_key
                )

            if resolution == "majority":
                majority_counts[column] += 1

            elif resolution == "tie":
                tie_counts[column] += 1
                tied_patients.add(
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

    report = {
        "patient_linkage_rows": int(
            len(linkage_table)
        ),
        "visit_rows_collapsed": int(
            len(cleaned_visits)
            - len(linkage_table)
        ),
        "patients_with_conflicting_linkage_values": int(
            len(conflicting_patients)
        ),
        "patients_with_unresolved_ties": int(
            len(tied_patients)
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

    return linkage_table, report


# ---------------------------------------------------------------------------
# Cleaning one hospital
# ---------------------------------------------------------------------------

def clean_dataframe(
    frame: pd.DataFrame,
    source_file: str,
    had_header: bool,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Clean one hospital DataFrame."""

    input_rows = len(frame)
    missing_before = blank_counts(frame)

    # Remove rows that are completely identical.
    exact_duplicate_mask = frame.duplicated(
        keep="first"
    )

    exact_duplicates_removed = int(
        exact_duplicate_mask.sum()
    )

    # the original CSV row numbers for rejected records.
    cleaned = (
        frame.loc[~exact_duplicate_mask]
        .copy()
    )

    cleaned, rejected_identity_rows = (
        remove_rows_without_ids(
            cleaned,
            had_header,
        )
    )

    hospital_id = validate_hospital_id(
        cleaned
    )

    cleaners: dict[str, Cleaner] = {
                "first_name": clean_name,
        "middle_name": clean_name,
        "last_name": clean_name,
        "email": clean_email,
        "date_of_birth": clean_date_of_birth,
        "sex": clean_sex,
        "postcode": clean_postcode,
        "phone": clean_phone,
        "fake_medicare_id": (
            clean_fake_medicare_id
        ),
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

    extra_columns = [
        column
        for column in cleaned.columns
        if column not in REQUIRED_COLUMNS
    ]

    cleaned = cleaned[
        REQUIRED_COLUMNS + extra_columns
    ]

    report: dict[str, object] = {
        "status": "success",
        "source_file": source_file,
        "input_had_header": had_header,
        "hospital_id": hospital_id,
        "input_rows": int(input_rows),
        "cleaned_visit_rows": int(
            len(cleaned)
        ),
        "exact_duplicate_rows_removed": (
            exact_duplicates_removed
        ),
        "rejected_identity_rows": (
            rejected_identity_rows
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
            "hospital_id",
        ],
        **patient_quality_metrics(cleaned),
        "privacy_note": (
            "The cleaned CSV contains identifying "
            "patient data and must remain local."
        ),
    }

    return cleaned, report


# ---------------------------------------------------------------------------
# Output writing
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


def get_output_paths(
    input_path: Path,
    cleaned_output_dir: Path,
    linkage_output_dir: Path,
    report_dir: Path,
) -> tuple[Path, Path, Path]:
    """Build the three output paths for one hospital."""

    cleaned_path = (
        cleaned_output_dir
        / f"{input_path.stem}_cleaned.csv"
    )

    linkage_path = (
        linkage_output_dir
        / (
            f"{input_path.stem}"
            "_linkage_patients.csv"
        )
    )

    report_path = (
        report_dir
        / (
            f"{input_path.stem}"
            "_cleaning_report.json"
        )
    )

    return (
        cleaned_path,
        linkage_path,
        report_path,
    )


def process_hospital(
    input_path: Path,
    cleaned_output_dir: Path,
    linkage_output_dir: Path,
    report_dir: Path,
    overwrite: bool,
) -> dict[str, object]:
    """Clean one hospital and write all its outputs."""

    (
        cleaned_path,
        linkage_path,
        report_path,
    ) = get_output_paths(
        input_path,
        cleaned_output_dir,
        linkage_output_dir,
        report_dir,
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

    cleaned_visits, report = clean_dataframe(
        frame,
        input_path.name,
        had_header,
    )

    (
        linkage_patients,
        consolidation_report,
    ) = build_patient_linkage_table(
        cleaned_visits
    )

    cleaned_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    linkage_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_dir.mkdir(
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

    report["cleaned_file"] = str(
        cleaned_path
    )

    report["linkage_patient_file"] = str(
        linkage_path
    )

    # report["patient_consolidation"] = (
    #     consolidation_report
    # )

    write_json(
        report,
        report_path,
    )

    return {
        "source_file": input_path.name,
        "hospital_id": report[
            "hospital_id"
        ],
        "status": "success",
        "input_rows": report[
            "input_rows"
        ],
        "cleaned_visit_rows": report[
            "cleaned_visit_rows"
        ],
        "patient_linkage_rows": (
            consolidation_report[
                "patient_linkage_rows"
            ]
        ),
        "exact_duplicates_removed": (
            report[
                "exact_duplicate_rows_removed"
            ]
        ),
        "identity_rows_rejected": (
            report[
                "rejected_identity_rows"
            ][
                "total_rows_rejected_for_missing_ids"
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
    """Discover current and future hospital CSV files."""

    if not input_dir.is_dir():
        raise CleaningError(
            "Data directory does not exist: "
            f"{input_dir}"
        )

    hospital_files = [
        path
        for path in input_dir.glob(pattern)
        if (
            path.is_file()
            and not path.stem.endswith(
                "_cleaned"
            )
            and "linkage_patients"
            not in path.stem
        )
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
# Command-line interface
# ---------------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clean all hospital CSV files"
        )
    )

    parser.add_argument(
                "--input-dir",
        type=Path,
        default=SRC_DIR.parent / "dataset",
        help=(
            "Directory containing hospital CSVs"
        ),
    )

    parser.add_argument(
        "--pattern",
        default="hospital*.csv",
        help=(
            "Filename pattern used to discover "
            "hospital files"
        ),
    )

    parser.add_argument(
        "--cleaned-output-dir",
        type=Path,
        default=DATA_DIR / "cleaned",
    )

    parser.add_argument(
        "--linkage-output-dir",
        type=Path,
        default=(
            DATA_DIR / "linkage_patients"
        ),
    )

    parser.add_argument(
        "--report-dir",
        type=Path,
        default=(
            DATA_DIR / "cleaning_reports"
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Replace previously generated "
            "cleaned files and reports"
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

    except CleaningError as exc:
        print(
            f"Cleaning failed: {exc}",
            file=sys.stderr,
        )

        return 1

    args.report_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    batch_report_path = (
        args.report_dir
        / "batch_cleaning_report.json"
    )

    if (
        batch_report_path.exists()
        and not args.overwrite
    ):
        print(
            "Cleaning failed: "
            f"{batch_report_path} already exists. "
            "Use --overwrite to run again.",
            file=sys.stderr,
        )

        return 1

    started = datetime.now(
        timezone.utc
    )

    successful_files: list[
        dict[str, object]
    ] = []

    failed_files: list[
        dict[str, str]
    ] = []

    for hospital_file in hospital_files:
        try:
            result = process_hospital(
                input_path=hospital_file,
                cleaned_output_dir=(
                    args.cleaned_output_dir
                ),
                linkage_output_dir=(
                    args.linkage_output_dir
                ),
                report_dir=args.report_dir,
                overwrite=args.overwrite,
            )

            successful_files.append(
                result
            )

            print(
                f"[OK] {hospital_file.name}: "
                f"{result['cleaned_visit_rows']} "
                "cleaned visit rows, "
                f"{result['patient_linkage_rows']} "
                "linkage patients, "
                f"{result['identity_rows_rejected']} "
                "invalid-ID rows rejected"
            )

        except (
            CleaningError,
            OSError,
            pd.errors.ParserError,
        ) as exc:
            failure = {
                "source_file": (
                    hospital_file.name
                ),
                "status": "failed",
                "error": str(exc),
            }

            failed_files.append(failure)

            # Produce an individual report even when
            # this hospital fails completely.
            failure_report_path = (
                args.report_dir
                / (
                    f"{hospital_file.stem}"
                    "_cleaning_report.json"
                )
            )

            if (
                args.overwrite
                or not failure_report_path.exists()
            ):
                write_json(
                    failure,
                    failure_report_path,
                )

            print(
                f"[FAILED] "
                f"{hospital_file.name}: {exc}",
                file=sys.stderr,
            )

    completed = datetime.now(
        timezone.utc
    )

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
        f"Batch report: {batch_report_path}"
    )

    return 1 if failed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
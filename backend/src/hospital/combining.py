#!/usr/bin/env python3
"""Create the requested patient-identifier combinations for hospitals."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "src" / "data" / "linkage_patients"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "src" / "data" / "combinations"

# Passed through unchanged so each row can be traced back to its patient.
ID_COLUMNS = ("hospital_id", "local_patient_id")

# Joins fields. Must not be "+", because phone numbers start with "+61".
SEPARATOR = "|"

REQUIRED_FIELDS = {
    "first_name",
    "last_name",
    "middle_name",
    "email",
    "date_of_birth",
    "sex",
    "postcode",
    "phone",
    "fake_medicare_id",
    "private_healthcare",
}

# Each tuple is (output column, ordered input fields/transformations).
COMBINATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('medicare_dob', ('fake_medicare_id', 'dob_iso')),
    ('private_dob', ('private_healthcare', 'dob_iso')),
    ('medicare_last_first_initial', ('fake_medicare_id', 'last_name', 'first_initial')),
    ('private_last_first_initial', ('private_healthcare', 'last_name', 'first_initial')),
    ('first_last_dob', ('first_name', 'last_name', 'dob_iso')),
    ('first_initial_last_dob_postcode', ('first_initial', 'last_name', 'dob_iso', 'postcode')),
    ('first_last_initial_dob_postcode', ('first_name', 'last_initial', 'dob_iso', 'postcode')),
    ('last_dob_postcode', ('last_name', 'dob_iso', 'postcode')),
    ('first_dob_postcode', ('first_name', 'dob_iso', 'postcode')),
    ('phone_dob', ('phone', 'dob_iso')),
    ('email_dob', ('email', 'dob_iso')),
    ('first_last_phone', ('first_name', 'last_name', 'phone')),
    ('first_last_email', ('first_name', 'last_name', 'email')),
    ('first_last_postcode', ('first_name', 'last_name', 'postcode')),
)


def _format_date(value: str, output_format: str) -> str:
    """Format a cleaned DOB, retaining blank values as blank."""
    value = value.strip()
    if not value:
        return ""

    for input_format in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value, input_format)
            return parsed.strftime(output_format)
        except ValueError:
            continue

    raise ValueError(f"Invalid date_of_birth value: {value!r}")


def build_combination_strings(row: Mapping[str, str]) -> dict[str, str]:
    """Build all 14 delimited combinations for one CSV row."""
    values = {field: (row.get(field) or "").strip() for field in REQUIRED_FIELDS}
    values.update(
        {
            "first_initial": values["first_name"][:1],
            "last_initial": values["last_name"][:1],
            "middle_initial": values["middle_name"][:1],
            "dob_iso": _format_date(values["date_of_birth"], "%Y-%m-%d"),
            "dob_mdy": _format_date(values["date_of_birth"], "%m/%d/%Y"),
        }
    )
    # Unknown sex is not evidence, so treat it as missing.
    if values["sex"] == "U":
        values["sex"] = ""

    combinations = {}
    for column, fields in COMBINATIONS:
        parts = [values[field] for field in fields]
        # Missing required identifiers must not become match evidence.
        combinations[column] = SEPARATOR.join(parts) if all(parts) else ""
    return combinations


def create_combination_csv(input_path: Path, output_path: Path) -> int:
    """Read a hospital CSV and write its combination columns; return row count."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different.")

    with input_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"Input CSV has no header: {input_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=[*ID_COLUMNS, *(column for column, _ in COMBINATIONS)],
            )
            writer.writeheader()
            row_count = 0
            for row in reader:
                ids = {column: (row.get(column) or "").strip() for column in ID_COLUMNS}
                writer.writerow({**ids, **build_combination_strings(row)})
                row_count += 1

    return row_count


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate patient-identifier combinations for every hospital."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    inputs = sorted(args.input_dir.glob("hospital*_linkage_patients.csv"))
    if not inputs:
        print(f"No linkage files found in {args.input_dir}. Run cleaning.py first.")
        return 1

    for input_path in inputs:
        name = input_path.stem.replace("_linkage_patients", "")
        output_path = args.output_dir / f"{name}_combinations.csv"
        rows = create_combination_csv(input_path, output_path)
        print(
            f"Wrote {len(COMBINATIONS)} combinations for {rows} rows to {output_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

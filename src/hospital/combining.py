#!/usr/bin/env python3
"""Create the requested patient-identifier combinations for hospitals."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "dataset" / "cleaned" / "hospital1_cleaned.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "dataset" / "cleaned" / "hospital1_combinations.csv"

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
}

# Each tuple is (output column, ordered input fields/transformations).
COMBINATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "first_last_middle_email_sex",
        ("first_name", "last_name", "middle_name", "email", "sex"),
    ),
    (
        "first_initial_last_initial_middle_initial_email_sex",
        ("first_initial", "last_initial", "middle_initial", "email", "sex"),
    ),
    (
        "last_first_middle_email_sex",
        ("last_name", "first_name", "middle_name", "email", "sex"),
    ),
    (
        "middle_last_first_email_sex",
        ("middle_name", "last_name", "first_name", "email", "sex"),
    ),
    ("first_last_fake_medicare_id", ("first_name", "last_name", "fake_medicare_id")),
    (
        "first_initial_last_initial_fake_medicare_id",
        ("first_initial", "last_initial", "fake_medicare_id"),
    ),
    ("last_first_fake_medicare_id", ("last_name", "first_name", "fake_medicare_id")),
    (
        "first_last_email_dob_dmy_sex_postcode_phone",
        ("first_name", "last_name", "email", "dob_dmy", "sex", "postcode", "phone"),
    ),
    (
        "last_first_email_dob_dmy_sex_postcode_phone",
        ("last_name", "first_name", "email", "dob_dmy", "sex", "postcode", "phone"),
    ),
    (
        "first_last_email_dob_mdy_sex_postcode_phone",
        ("first_name", "last_name", "email", "dob_mdy", "sex", "postcode", "phone"),
    ),
    (
        "first_initial_last_initial_email_dob_dmy_sex_postcode_phone",
        (
            "first_initial",
            "last_initial",
            "email",
            "dob_dmy",
            "sex",
            "postcode",
            "phone",
        ),
    ),
    (
        "first_middle_email_sex_phone",
        ("first_name", "middle_name", "email", "sex", "phone"),
    ),
    (
        "first_initial_middle_initial_sex_phone",
        ("first_initial", "middle_initial", "sex", "phone"),
    ),
    (
        "middle_first_email_sex_phone",
        ("middle_name", "first_name", "email", "sex", "phone"),
    ),
    (
        "first_last_middle_dob_dmy_phone",
        ("first_name", "last_name", "middle_name", "dob_dmy", "phone"),
    ),
    (
        "first_initial_last_initial_middle_initial_dob_mdy_phone",
        ("first_initial", "last_initial", "middle_initial", "dob_mdy", "phone"),
    ),
    (
        "last_middle_first_dob_dmy_phone",
        ("last_name", "middle_name", "first_name", "dob_dmy", "phone"),
    ),
    (
        "middle_first_last_dob_dmy_phone",
        ("middle_name", "first_name", "last_name", "dob_dmy", "phone"),
    ),
    (
        "email_dob_dmy_sex_postcode_phone_fake_medicare_id",
        ("email", "dob_dmy", "sex", "postcode", "phone", "fake_medicare_id"),
    ),
    (
        "email_dob_mdy_sex_postcode_phone_fake_medicare_id",
        ("email", "dob_mdy", "sex", "postcode", "phone", "fake_medicare_id"),
    ),
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
    """Build all 20 delimited combinations for one CSV row."""
    values = {field: (row.get(field) or "").strip() for field in REQUIRED_FIELDS}
    values.update(
        {
            "first_initial": values["first_name"][:1],
            "last_initial": values["last_name"][:1],
            "middle_initial": values["middle_name"][:1],
            "dob_dmy": _format_date(values["date_of_birth"], "%d/%m/%Y"),
            "dob_mdy": _format_date(values["date_of_birth"], "%m/%d/%Y"),
        }
    )
    return {
        column: "+".join(values[field] for field in fields)
        for column, fields in COMBINATIONS
    }


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
                fieldnames=[column for column, _ in COMBINATIONS],
            )
            row_count = 0
            for row in reader:
                writer.writerow(build_combination_strings(row))
                row_count += 1

    return row_count


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate 20 patient-identifier combinations from a hospital CSV."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    rows = create_combination_csv(args.input, args.output)
    print(f"Wrote {len(COMBINATIONS)} combinations for {rows} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate protected linkage tokens locally at each hospital node.

Pipeline position:

    hospitalN.csv -> cleaning.py -> linkage_patients/hospitalN_linkage_patients.csv
                  -> token_generator.py -> tokens/hospitalN_tokens.csv -> broker

Input is the cleaned, one-row-per-patient linkage file written by cleaning.py.
This script does NOT clean data. It checks that values are already in the
cleaned format and stops if they are not, so cleaning rules live in one place.

Output contains only site_id, local_patient_id and token columns. No names,
dates of birth, phone numbers, postcodes or Medicare numbers leave this script.

Token rules (Tasks document, "Example token rules"):

    token_1 = first_name + last_name + date_of_birth + sex
    token_2 = last_name + date_of_birth + postcode
    token_3 = first_initial + last_name + date_of_birth
    token_4 = phone + date_of_birth
    token_5 = fake_medicare_id + date_of_birth

Encoding methods:

    hmac_sha256   (default) HMAC-SHA256 with a secret key shared by all
                  hospitals and never given to the broker.
    sha256        Plain SHA-256. No key. Reversible by dictionary attack.
    sha512        Plain SHA-512. No key. Same weakness as sha256.
    salted_sha256 SHA-256 of (shared salt + message). Included for the
                  security comparison only.

The unkeyed methods exist so the privacy and security work can compare them
against HMAC using identical inputs. Use hmac_sha256 for real linkage runs.

Usage:

    python src/hospital/token_generator.py --generate-key secrets/pprl_token.key
    python src/hospital/token_generator.py --key-file secrets/pprl_token.key
    python src/hospital/token_generator.py --method sha256 --output-dir src/data/tokens_sha256
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from datetime import date, datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# token_generator.py is expected at src/hospital/token_generator.py
SRC_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = SRC_DIR / "data"

KEY_ENV_VAR = "PPRL_TOKEN_KEY"


# ---------------------------------------------------------------------------
# Token configuration
# ---------------------------------------------------------------------------

# Every hospital must use exactly these rules, in exactly this field order.
# Changing a rule changes every token it produces, so all hospitals must
# change together and re-tokenise.
TOKEN_RULES: dict[str, tuple[str, ...]] = {
    "token_1": ("first_name", "last_name", "date_of_birth", "sex"),
    "token_2": ("last_name", "date_of_birth", "postcode"),
    "token_3": ("first_initial", "last_name", "date_of_birth"),
    "token_4": ("phone", "date_of_birth"),
    "token_5": ("fake_medicare_id", "date_of_birth"),
}

# Bump this when TOKEN_RULES or the message format changes. It is mixed into
# every token, so tokens from different rule versions can never match.
TOKEN_SCHEME_VERSION = "v1"

# Unit separator: cannot appear in any cleaned value (see FIELD_VALIDATORS),
# so "ann"+"abel" and "anna"+"bel" produce different messages.
FIELD_SEPARATOR = "\x1f"

METHODS = ("hmac_sha256", "sha256", "sha512", "salted_sha256")
KEYED_METHODS = ("hmac_sha256", "salted_sha256")
MIN_KEY_BYTES = 32

# Columns cleaning.py writes to the linkage file.
INPUT_ID_COLUMNS = ("hospital_id", "local_patient_id")
INPUT_LINKAGE_COLUMNS = (
    "first_name",
    "last_name",
    "date_of_birth",
    "sex",
    "postcode",
    "phone",
    "fake_medicare_id",
)


class TokenisationError(ValueError):
    """Raised when a linkage file cannot be tokenised safely."""


# ---------------------------------------------------------------------------
# Input validation (checks cleaned format; never modifies values)
# ---------------------------------------------------------------------------

def _is_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


FIELD_VALIDATORS = {
    "first_name": lambda v: re.fullmatch(r"[a-z]+", v) is not None,
    "last_name": lambda v: re.fullmatch(r"[a-z]+", v) is not None,
    "date_of_birth": _is_iso_date,
    "sex": lambda v: v in {"M", "F", "O", "U"},
    "postcode": lambda v: re.fullmatch(r"\d{4}", v) is not None,
    "phone": lambda v: re.fullmatch(r"\+61\d{9}", v) is not None,
    "fake_medicare_id": lambda v: re.fullmatch(r"[A-Za-z0-9]+", v) is not None,
}

FIELD_FORMAT_HINTS = {
    "first_name": "lowercase letters only",
    "last_name": "lowercase letters only",
    "date_of_birth": "YYYY-MM-DD",
    "sex": "M, F, O or U",
    "postcode": "four digits",
    "phone": "+61 followed by nine digits",
    "fake_medicare_id": "letters and digits only",
}


def read_linkage_file(path: Path) -> list[dict[str, str]]:
    """Read a cleaning.py linkage file and validate its structure and values.

    Error messages report row numbers and field names only, never values,
    so they are safe to paste into issues or chat.
    """

    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            rows = [
                {key: (value or "").strip() for key, value in row.items() if key is not None}
                for row in reader
            ]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise TokenisationError(f"Could not read {path}: {exc}") from exc

    missing = [c for c in (*INPUT_ID_COLUMNS, *INPUT_LINKAGE_COLUMNS) if c not in header]
    if missing:
        raise TokenisationError(
            f"{path.name} is missing columns: {', '.join(missing)}. "
            "Expected the linkage_patients file written by cleaning.py."
        )

    if not rows:
        raise TokenisationError(f"{path.name} has no data rows")

    hospital_ids = {row["hospital_id"] for row in rows}
    if len(hospital_ids) != 1 or "" in hospital_ids:
        raise TokenisationError(
            f"{path.name} must contain exactly one non-blank hospital_id; "
            f"found {len(hospital_ids)} distinct values"
        )

    seen_ids: dict[str, int] = {}
    problems: list[str] = []

    for row_number, row in enumerate(rows, start=2):  # row 1 is the header
        local_id = row["local_patient_id"]
        if not local_id:
            problems.append(f"row {row_number}: blank local_patient_id")
        elif local_id in seen_ids:
            problems.append(
                f"row {row_number}: local_patient_id duplicates row {seen_ids[local_id]} "
                "(linkage file must have one row per patient)"
            )
        else:
            seen_ids[local_id] = row_number

        for field, is_valid in FIELD_VALIDATORS.items():
            value = row[field]
            if value and not is_valid(value):
                problems.append(
                    f"row {row_number}: {field} is not in cleaned format "
                    f"({FIELD_FORMAT_HINTS[field]})"
                )

    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise TokenisationError(
            f"{path.name} failed validation ({len(problems)} problems). "
            f"Re-run cleaning.py or fix the input.\n  {shown}{more}"
        )

    return rows


# ---------------------------------------------------------------------------
# Token construction
# ---------------------------------------------------------------------------

def linkage_values(row: dict[str, str]) -> dict[str, str]:
    """Return the field values tokens are built from.

    Derived fields are computed here. 'U' (unknown sex) is treated as missing:
    two records both lacking a sex value is not evidence they are the same
    person, so it must not contribute to a match.
    """

    values = {field: row[field] for field in INPUT_LINKAGE_COLUMNS}
    values["first_initial"] = values["first_name"][:1]
    if values["sex"] == "U":
        values["sex"] = ""
    return values


def build_message(token_name: str, values: dict[str, str]) -> str | None:
    """Build the string to be encoded for one token, or None if not computable.

    A token with any missing field is not produced. Otherwise, e.g.,
    phone + DOB for two patients with no phone would collapse to DOB alone
    and falsely match everyone born on the same day.
    """

    fields = TOKEN_RULES[token_name]
    parts = [values[field] for field in fields]
    if any(part == "" for part in parts):
        return None
    return FIELD_SEPARATOR.join((TOKEN_SCHEME_VERSION, token_name, *parts))


def encode(message: str, method: str, key: bytes | None) -> str:
    data = message.encode("utf-8")
    if method == "hmac_sha256":
        return hmac.new(key, data, hashlib.sha256).hexdigest()
    if method == "salted_sha256":
        return hashlib.sha256(key + data).hexdigest()
    if method == "sha256":
        return hashlib.sha256(data).hexdigest()
    if method == "sha512":
        return hashlib.sha512(data).hexdigest()
    raise ValueError(f"Unknown method: {method}")


def tokenise_row(row: dict[str, str], method: str, key: bytes | None) -> dict[str, str]:
    values = linkage_values(row)
    tokens: dict[str, str] = {}
    for token_name in TOKEN_RULES:
        message = build_message(token_name, values)
        tokens[token_name] = "" if message is None else encode(message, method, key)
    return tokens


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def parse_key(text: str, source: str) -> bytes:
    """Keys are stored as hex. Reject anything too short to be a real secret."""

    text = text.strip()
    try:
        key = bytes.fromhex(text)
    except ValueError as exc:
        raise TokenisationError(f"Key from {source} is not valid hex") from exc
    if len(key) < MIN_KEY_BYTES:
        raise TokenisationError(
            f"Key from {source} is {len(key)} bytes; need at least {MIN_KEY_BYTES}"
        )
    return key


def load_key(key_file: Path | None) -> bytes:
    if key_file is not None:
        try:
            return parse_key(key_file.read_text(encoding="utf-8"), str(key_file))
        except OSError as exc:
            raise TokenisationError(f"Could not read key file {key_file}: {exc}") from exc
    env_value = os.environ.get(KEY_ENV_VAR)
    if env_value:
        return parse_key(env_value, f"${KEY_ENV_VAR}")
    raise TokenisationError(
        f"No key supplied. Use --key-file or set {KEY_ENV_VAR}. "
        "Create one with --generate-key."
    )


def generate_key_file(path: Path) -> None:
    if path.exists():
        raise TokenisationError(f"{path} already exists; refusing to overwrite a key")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secrets.token_hex(MIN_KEY_BYTES) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows ignores POSIX permissions


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def output_paths(input_path: Path, output_dir: Path, report_dir: Path) -> tuple[Path, Path]:
    stem = input_path.stem.removesuffix("_linkage_patients")
    return (
        output_dir / f"{stem}_tokens.csv",
        report_dir / f"{stem}_token_report.json",
    )


def tokenise_file(
    input_path: Path,
    output_dir: Path,
    report_dir: Path,
    method: str,
    key: bytes | None,
    overwrite: bool,
) -> dict[str, object]:
    token_path, report_path = output_paths(input_path, output_dir, report_dir)

    if not overwrite:
        for path in (token_path, report_path):
            if path.exists():
                raise TokenisationError(
                    f"Output already exists: {path}. Use --overwrite to replace it."
                )

    rows = read_linkage_file(input_path)
    site_id = rows[0]["hospital_id"]

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    produced = {name: 0 for name in TOKEN_RULES}
    no_tokens = 0

    with token_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["site_id", "local_patient_id", *TOKEN_RULES])
        for row in rows:
            tokens = tokenise_row(row, method, key)
            for name, value in tokens.items():
                produced[name] += int(bool(value))
            if not any(tokens.values()):
                no_tokens += 1
            writer.writerow([site_id, row["local_patient_id"], *tokens.values()])

    total = len(rows)
    report: dict[str, object] = {
        "source_file": input_path.name,
        "token_file": str(token_path),
        "site_id": site_id,
        "method": method,
        "token_scheme_version": TOKEN_SCHEME_VERSION,
        "token_rules": {name: list(fields) for name, fields in TOKEN_RULES.items()},
        "patients": total,
        "tokens_produced": produced,
        "tokens_blank_due_to_missing_fields": {n: total - c for n, c in produced.items()},
        "patients_with_no_tokens": no_tokens,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "privacy_note": (
            "Token file holds site_id, local_patient_id and encoded tokens only. "
            + (
                "Tokens are keyed; the broker must never receive the key."
                if method in KEYED_METHODS
                else "WARNING: unkeyed method. Tokens can be reversed by dictionary "
                "attack. Use for comparison experiments only."
            )
        ),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def natural_sort_key(path: Path) -> list[object]:
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", path.name)]


def discover_inputs(input_dir: Path, pattern: str) -> list[Path]:
    if not input_dir.is_dir():
        raise TokenisationError(f"Input directory does not exist: {input_dir}")
    files = sorted((p for p in input_dir.glob(pattern) if p.is_file()), key=natural_sort_key)
    if not files:
        raise TokenisationError(f"No files matching {pattern!r} in {input_dir}. Run cleaning.py first.")
    return files


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------

def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PPRL tokens from cleaned linkage files")
    parser.add_argument("--input-dir", type=Path, default=DATA_DIR / "linkage_patients")
    parser.add_argument("--pattern", default="hospital*_linkage_patients.csv")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR / "tokens")
    parser.add_argument("--report-dir", type=Path, default=DATA_DIR / "token_reports")
    parser.add_argument("--method", choices=METHODS, default="hmac_sha256")
    parser.add_argument("--key-file", type=Path, help=f"hex key file (or set ${KEY_ENV_VAR})")
    parser.add_argument("--generate-key", type=Path, metavar="PATH",
                        help="write a new random shared key to PATH and exit")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)

    try:
        if args.generate_key:
            generate_key_file(args.generate_key)
            print(
                f"Wrote key to {args.generate_key}. Give it to every hospital, "
                "never to the broker, and never commit it."
            )
            return 0

        key = load_key(args.key_file) if args.method in KEYED_METHODS else None
        if args.method not in KEYED_METHODS:
            print(
                f"WARNING: {args.method} is unkeyed and reversible by dictionary attack. "
                "Use only for comparison experiments.",
                file=sys.stderr,
            )

        inputs = discover_inputs(args.input_dir, args.pattern)
    except TokenisationError as exc:
        print(f"Tokenisation failed: {exc}", file=sys.stderr)
        return 1

    failures = 0
    site_ids: dict[str, str] = {}
    for path in inputs:
        try:
            report = tokenise_file(
                path, args.output_dir, args.report_dir, args.method, key, args.overwrite
            )
        except TokenisationError as exc:
            failures += 1
            print(f"[FAILED] {path.name}: {exc}", file=sys.stderr)
            continue

        site = str(report["site_id"])
        if site in site_ids:
            print(
                f"[WARNING] {path.name} has site_id {site}, same as {site_ids[site]}",
                file=sys.stderr,
            )
        site_ids[site] = path.name

        produced = report["tokens_produced"]
        summary = ", ".join(f"{n}={c}" for n, c in produced.items())
        print(f"[OK] {path.name}: {report['patients']} patients -> {report['token_file']} ({summary})")

    print(f"Finished: {len(inputs) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

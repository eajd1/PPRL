#!/usr/bin/env python3
"""Hash the combinations made by combining.py into protected tokens.

Pipeline position:

    cleaning.py -> combining.py -> token_generator.py -> broker
                src/data/combinations/   src/data/tokens/

This script only hashes. It does not clean data or decide which fields are
combined; that is done by cleaning.py and combining.py.

Input  (one file per hospital, made by combining.py):
    hospital_id, local_patient_id, <combination 1>, <combination 2>, ...

Output (sent to the broker):
    site_id, local_patient_id, <token 1>, <token 2>, ...

Each token column has the same name as the combination it came from.
A blank combination (a field was missing) stays blank, so the broker skips it.
local_patient_id is copied unchanged and is never hashed: it is a local label
each hospital uses to find its own patient when results come back.

Methods:
    hmac_sha256    (default) HMAC-SHA256 with a key shared by all hospitals
                and never given to the broker.
    sha256, sha512 Plain hashes with no key. Anyone can recompute them from
                guessed names and birth dates (dictionary attack).
    salted_sha256  SHA-256 of key + text.
The unkeyed methods are for the security comparison only.

Usage:
    python src/hospital/token_generator.py --generate-key     (once, ever)
    python src/hospital/token_generator.py                    (every run)
    python src/hospital/token_generator.py --method sha256 --output-dir src/data/tokens_sha256

Run button: pressing Run in VS Code (or running with no options) first runs
cleaning.py and combining.py, then hashes, so the whole hospital side runs in
one click. Set RUN_EARLIER_STEPS = False to only hash. Running with any option
(such as --method) only hashes.
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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# token_generator.py is expected at src/hospital/token_generator.py
SRC_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = SRC_DIR / "data"
DEFAULT_KEY_FILE = SRC_DIR.parent / "secrets" / "pprl_token.key"
KEY_ENV_VAR = "PPRL_TOKEN_KEY"

# When True, pressing Run (no options) also runs cleaning.py and combining.py
# first, so one click does the whole hospital side.
RUN_EARLIER_STEPS = True
EARLIER_STEPS = (
    ("cleaning", [SRC_DIR / "hospital" / "cleaning.py", "--overwrite"]),
    ("combining", [SRC_DIR / "hospital" / "combining.py"]),
)

ID_COLUMNS = ("hospital_id", "local_patient_id")
METHODS = ("hmac_sha256", "sha256", "sha512", "salted_sha256")
KEYED_METHODS = ("hmac_sha256", "salted_sha256")
MIN_KEY_BYTES = 32

# Bump this if the hashing input format changes, so old and new tokens
# can never match by accident.
TOKEN_SCHEME_VERSION = "v2"

# Separates the version, combination name and value before hashing.
# It cannot appear in cleaned data.
FIELD_SEPARATOR = "\x1f"


class TokenisationError(ValueError):
    """Raised when a combinations file cannot be tokenised safely."""


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

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


def make_token(combination_name: str, value: str, method: str, key: bytes | None) -> str:
    """Hash one combination. Blank stays blank.

    The combination name is part of the hashed text, so the same value in two
    different combinations never produces the same token.
    """

    if not value:
        return ""
    message = FIELD_SEPARATOR.join((TOKEN_SCHEME_VERSION, combination_name, value))
    return encode(message, method, key)


# ---------------------------------------------------------------------------
# Reading combinations
# ---------------------------------------------------------------------------

def read_combinations(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return (combination column names, rows). Errors never include values."""

    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header = [name.strip() for name in (reader.fieldnames or [])]
            reader.fieldnames = header
            rows = [
                {k: (v or "").strip() for k, v in row.items() if k is not None}
                for row in reader
            ]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise TokenisationError(f"Could not read {path}: {exc}") from exc

    missing = [c for c in ID_COLUMNS if c not in header]
    if missing:
        raise TokenisationError(
            f"{path.name} is missing {', '.join(missing)}. "
            "Expected the combinations file written by combining.py."
        )

    combination_columns = [c for c in header if c not in ID_COLUMNS]
    if not combination_columns:
        raise TokenisationError(f"{path.name} has no combination columns")
    if len(set(combination_columns)) != len(combination_columns):
        raise TokenisationError(f"{path.name} has two combination columns with the same name")
    if not rows:
        raise TokenisationError(f"{path.name} has no data rows")

    hospital_ids = {row["hospital_id"] for row in rows}
    if len(hospital_ids) != 1 or "" in hospital_ids:
        raise TokenisationError(
            f"{path.name} must contain exactly one non-blank hospital_id; "
            f"found {len(hospital_ids)} distinct values"
        )

    seen: dict[str, int] = {}
    for row_number, row in enumerate(rows, start=2):  # row 1 is the header
        local_id = row["local_patient_id"]
        if not local_id:
            raise TokenisationError(f"{path.name} row {row_number}: blank local_patient_id")
        if local_id in seen:
            raise TokenisationError(
                f"{path.name} row {row_number}: local_patient_id repeats row "
                f"{seen[local_id]} (expected one row per patient)"
            )
        seen[local_id] = row_number

    return combination_columns, rows


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def parse_key(text: str, source: str) -> bytes:
    try:
        key = bytes.fromhex(text.strip())
    except ValueError as exc:
        raise TokenisationError(f"Key from {source} is not valid hex") from exc
    if len(key) < MIN_KEY_BYTES:
        raise TokenisationError(f"Key from {source} is {len(key)} bytes; need at least {MIN_KEY_BYTES}")
    return key


def load_key(key_file: Path | None) -> bytes:
    """Key lookup order: --key-file, then $PPRL_TOKEN_KEY, then the default file."""

    if key_file is None and os.environ.get(KEY_ENV_VAR):
        return parse_key(os.environ[KEY_ENV_VAR], f"${KEY_ENV_VAR}")
    path = key_file or DEFAULT_KEY_FILE
    if not path.is_file():
        raise TokenisationError(
            f"No key found at {path}. Create one once with --generate-key."
        )
    return parse_key(path.read_text(encoding="utf-8"), str(path))


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

def tokenise_file(
    input_path: Path,
    output_dir: Path,
    report_dir: Path,
    method: str,
    key: bytes | None,
) -> dict[str, object]:
    combination_columns, rows = read_combinations(input_path)
    site_id = rows[0]["hospital_id"]
    stem = input_path.stem.removesuffix("_combinations")
    token_path = output_dir / f"{stem}_tokens.csv"
    report_path = report_dir / f"{stem}_token_report.json"

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    produced = {name: 0 for name in combination_columns}
    with token_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["site_id", "local_patient_id", *combination_columns])
        for row in rows:
            tokens = [make_token(name, row[name], method, key) for name in combination_columns]
            for name, token in zip(combination_columns, tokens):
                produced[name] += int(bool(token))
            writer.writerow([site_id, row["local_patient_id"], *tokens])

    report = {
        "source_file": input_path.name,
        "token_file": str(token_path),
        "site_id": site_id,
        "method": method,
        "token_scheme_version": TOKEN_SCHEME_VERSION,
        "patients": len(rows),
        "tokens_produced": produced,
        "patients_with_no_tokens": sum(
            1 for row in rows if not any(row[name] for name in combination_columns)
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "privacy_note": (
            "Keyed tokens; the broker must never receive the key."
            if method in KEYED_METHODS
            else "WARNING: unkeyed method, reversible by dictionary attack. Comparison only."
        ),
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def natural_sort_key(path: Path) -> list[object]:
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", path.name)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hash combinations into PPRL tokens")
    parser.add_argument("--input-dir", type=Path, default=DATA_DIR / "combinations")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR / "tokens")
    parser.add_argument("--report-dir", type=Path, default=DATA_DIR / "token_reports")
    parser.add_argument("--method", choices=METHODS, default="hmac_sha256")
    parser.add_argument("--key-file", type=Path, help="hex key file (default: secrets/pprl_token.key)")
    parser.add_argument("--generate-key", type=Path, nargs="?", const=DEFAULT_KEY_FILE, metavar="PATH",
                        help="write a new shared key (default secrets/pprl_token.key) and exit")
    args = parser.parse_args(argv)

    try:
        if args.generate_key:
            generate_key_file(args.generate_key)
            print(f"Wrote key to {args.generate_key}. Give it to every hospital, "
                "never to the broker, and never commit it.")
            return 0

        key = load_key(args.key_file) if args.method in KEYED_METHODS else None
        if key is None:
            print(f"WARNING: {args.method} is unkeyed and reversible by dictionary attack. "
                "Use only for comparison.", file=sys.stderr)

        inputs = sorted(args.input_dir.glob("hospital*_combinations.csv"), key=natural_sort_key)
        if not inputs:
            raise TokenisationError(f"No combinations files in {args.input_dir}. Run combining.py first.")
    except TokenisationError as exc:
        print(f"Tokenisation failed: {exc}", file=sys.stderr)
        return 1

    failures = 0
    for path in inputs:
        try:
            report = tokenise_file(path, args.output_dir, args.report_dir, args.method, key)
        except TokenisationError as exc:
            failures += 1
            print(f"[FAILED] {path.name}: {exc}", file=sys.stderr)
            continue
        made = sum(report["tokens_produced"].values())
        print(f"[OK] {path.name}: {report['patients']} patients, {made} tokens -> {report['token_file']}")

    print(f"Finished: {len(inputs) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


def run_earlier_steps() -> int:
    """Run cleaning.py then combining.py; stop at the first failure."""

    for name, (script, *options) in EARLIER_STEPS:
        print(f"\n=== {name} ===")
        if not script.is_file():
            print(f"Missing {script}", file=sys.stderr)
            return 1
        result = subprocess.run([sys.executable, str(script), *options], cwd=SRC_DIR.parent)
        if result.returncode != 0:
            print(f"Stopped: {name} failed (see messages above).", file=sys.stderr)
            return result.returncode
    print("\n=== tokenisation ===")
    return 0


if __name__ == "__main__":
    if RUN_EARLIER_STEPS and len(sys.argv) == 1:
        code = run_earlier_steps()
        if code != 0:
            raise SystemExit(code)
    raise SystemExit(main())

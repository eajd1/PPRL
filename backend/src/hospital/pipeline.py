#!/usr/bin/env python3
"""Reusable hospital pipeline and command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support both direct CLI execution and normal package imports in FastAPI.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.hospital import cleaning, combining, token_generator as tokens


def validate_configuration() -> bytes:
    key = tokens.load_key(None)
    if not tokens.TOKEN_KEY_ID or not tokens.re.fullmatch(r"[A-Za-z0-9_.-]+", tokens.TOKEN_KEY_ID):
        raise tokens.TokenisationError("Set a valid PPRL_KEY_ID in the shared .env configuration")
    if not tokens.TOKEN_SCHEME_VERSION.strip() or tokens.FIELD_SEPARATOR in tokens.TOKEN_SCHEME_VERSION:
        raise tokens.TokenisationError("Set a valid PPRL_TOKEN_VERSION in the shared .env configuration")
    return key


def process_file(source: Path, data_dir: Path, *, overwrite: bool = False,
                 key: bytes | None = None) -> dict:
    """Clean, combine and tokenise exactly one CSV; no subprocesses or discovery."""
    key = validate_configuration() if key is None else key
    summary = cleaning.process_hospital(
        source, data_dir / "cleaned", data_dir / "linkage_patients",
        data_dir / "cleaning_reports", overwrite,
    )
    combination_path = data_dir / "combinations" / f"{source.stem}_combinations.csv"
    combining.create_combination_csv(Path(summary["linkage_patient_file"]), combination_path)
    report = tokens.tokenise_file(
        combination_path, data_dir / "tokens", data_dir / "token_reports", "hmac_sha256", key,
    )
    return {**summary, "token_file": report["token_file"],
            "token_report_file": str(data_dir / "token_reports" / f"{source.stem}_token_report.json"),
            "tokens_generated": sum(report["tokens_produced"].values()),
            "patients_with_no_tokens": report["patients_with_no_tokens"],
            "token_version": tokens.TOKEN_SCHEME_VERSION, "key_id": tokens.TOKEN_KEY_ID}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=tokens.SRC_DIR.parent / "dataset")
    parser.add_argument("--data-dir", type=Path, default=tokens.DATA_DIR)
    parser.add_argument("--pattern", default="hospital*.csv")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        key = validate_configuration()
        inputs = cleaning.discover_hospitals(args.input_dir, args.pattern)
    except (cleaning.CleaningError, tokens.TokenisationError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    results = []
    for source in inputs:
        try:
            summary = process_file(source, args.data_dir, overwrite=args.overwrite, key=key)
            results.append(summary)
            print(f"[OK] {source.name}: {summary['patient_linkage_rows']} patients tokenised")
        except (ValueError, OSError) as exc:
            results.append({"source_file": source.name, "status": "failed", "error": str(exc)})
            print(f"[FAILED] {source.name}: {exc}", file=sys.stderr)
    failures = sum(item["status"] != "success" for item in results)
    cleaning.write_json(
        {"total_hospitals": len(inputs), "successful_hospitals": len(inputs) - failures,
         "failed_hospitals": failures, "hospitals": results},
        args.data_dir / "cleaning_reports" / "batch_cleaning_report.json",
    )
    print(f"Finished: {len(inputs) - failures} succeeded, {failures} failed.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

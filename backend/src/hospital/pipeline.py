#!/usr/bin/env python3
"""Reusable hospital pipeline and command-line entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support both direct CLI execution and normal package imports in FastAPI.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.hospital import cleaning, combining, token_generator as tokens


def validate_configuration() -> bytes:
    if tokens.TOKEN_SCHEME_VERSION != "v4":
        raise tokens.TokenisationError("Set PPRL_TOKEN_VERSION=v4 and regenerate all hospital tokens")
    key = tokens.load_key(None)
    if not tokens.TOKEN_KEY_ID or not tokens.re.fullmatch(r"[A-Za-z0-9_.-]+", tokens.TOKEN_KEY_ID):
        raise tokens.TokenisationError("Set a valid PPRL_KEY_ID in the shared .env configuration")
    if not tokens.TOKEN_SCHEME_VERSION.strip() or tokens.FIELD_SEPARATOR in tokens.TOKEN_SCHEME_VERSION:
        raise tokens.TokenisationError("Set a valid PPRL_TOKEN_VERSION in the shared .env configuration")
    return key


def prepare_file(source: Path, data_dir: Path, *, overwrite: bool = False) -> dict:
    """Preserve cleaned visits and prepare local conflict information."""
    return cleaning.process_hospital(
        source, data_dir / "cleaned", data_dir / "linkage_patients",
        data_dir / "cleaning_reports", overwrite,
    )


def finalize_file(summary: dict, data_dir: Path, *, key: bytes | None = None,
                  resolutions: dict | None = None) -> dict:
    """Generate tokens once the prepared review has explicit local decisions."""
    key = validate_configuration() if key is None else key
    summary = dict(summary)
    source = Path(summary["source_file"])
    conflicts = json.loads(Path(summary["conflicts_file"]).read_text(encoding="utf-8"))["patients"]
    if conflicts and resolutions is None:
        raise ValueError("Resolve consolidation conflicts before generating tokens")
    if resolutions is not None:
        cleaning.validate_patient_resolutions(conflicts, resolutions)
        linkage_path = data_dir / "linkage_patients" / Path(summary["linkage_patient_file"]).name
        consolidation = cleaning.resolve_patient_linkage(
            Path(summary["cleaned_file"]), linkage_path, resolutions,
            review_ties_only=summary.get("consolidation_review_policy") == "ties_only")
        report = json.loads(Path(summary["report_file"]).read_text(encoding="utf-8"))
        report.update(linkage_patient_file=str(linkage_path), patient_consolidation=consolidation,
                      consolidation_review={"status": "confirmed",
                          "resolved_fields": sum(len(fields) for fields in resolutions.values())})
        report_path = data_dir / "cleaning_reports" / Path(summary["report_file"]).name
        cleaning.write_json(report, report_path)
        cleaning.write_json({"resolutions": resolutions},
                            data_dir / "cleaning_reports" / "consolidation_resolutions.json")
        summary.update(linkage_patient_file=str(linkage_path), report_file=str(report_path),
                       resolved_conflict_fields=sum(len(fields) for fields in resolutions.values()))
    combination_path = data_dir / "combinations" / f"{source.stem}_combinations.csv"
    combining.create_combination_csv(Path(summary["linkage_patient_file"]), combination_path)
    report = tokens.tokenise_file(
        combination_path, data_dir / "tokens", data_dir / "token_reports", "hmac_sha256", key,
    )
    return {**summary, "status": "success", "token_file": report["token_file"],
            "token_report_file": str(data_dir / "token_reports" / f"{source.stem}_token_report.json"),
            "tokens_generated": sum(report["tokens_produced"].values()),
            "patients_with_no_tokens": report["patients_with_no_tokens"],
            "token_version": tokens.TOKEN_SCHEME_VERSION, "key_id": tokens.TOKEN_KEY_ID}


def process_file(source: Path, data_dir: Path, *, overwrite: bool = False,
                key: bytes | None = None) -> dict:
    """Use unique most-frequent values automatically; pause only tied maxima."""
    key = validate_configuration() if key is None else key
    summary = prepare_file(source, data_dir, overwrite=overwrite)
    if summary["conflict_patients"]:
        return {**summary, "status": "awaiting_review",
                "token_version": tokens.TOKEN_SCHEME_VERSION, "key_id": tokens.TOKEN_KEY_ID}
    return finalize_file(summary, data_dir, key=key)


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
            if summary["status"] == "awaiting_review":
                print(f"[REVIEW] {source.name}: {summary['conflict_patients']} patients need local review; no tokens generated. Upload the original CSV to the hospital web application as a new job to review and generate tokens.")
            else:
                print(f"[OK] {source.name}: {summary['patient_linkage_rows']} patients tokenised")
        except (ValueError, OSError) as exc:
            results.append({"source_file": source.name, "status": "failed", "error": str(exc)})
            print(f"[FAILED] {source.name}: {exc}", file=sys.stderr)
    failures = sum(item["status"] == "failed" for item in results)
    waiting = sum(item["status"] == "awaiting_review" for item in results)
    cleaning.write_json(
        {"total_hospitals": len(inputs), "successful_hospitals": len(inputs) - failures - waiting,
         "failed_hospitals": failures, "awaiting_review_hospitals": waiting, "hospitals": results},
        args.data_dir / "cleaning_reports" / "batch_cleaning_report.json",
    )
    print(f"Finished: {len(inputs) - failures - waiting} succeeded, {waiting} awaiting review, {failures} failed.")
    return 1 if failures else 2 if waiting else 0


if __name__ == "__main__":
    raise SystemExit(main())

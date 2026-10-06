#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLEANING = ROOT / "src" / "hospital" / "cleaning.py"
TOKENISER = ROOT / "src" / "hospital" / "token_generator.py"
DEFAULT_KEY = ROOT / "secrets" / "pprl_token.key"
KEYED_METHODS = {"hmac_sha256", "salted_sha256"}


def run(step: str, command: list[str]) -> None:
    print(f"\n=== {step} ===")
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print(f"\nStopped: {step} failed (see messages above).", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean and tokenise all hospital files")
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY)
    parser.add_argument(
        "--method",
        default="hmac_sha256",
        choices=["hmac_sha256", "sha256", "sha512", "salted_sha256"],
    )
    args = parser.parse_args()

    for script in (CLEANING, TOKENISER):
        if not script.is_file():
            sys.exit(f"Missing {script.relative_to(ROOT)}. Run this from the PPRL folder on the tokenization branch.")

    token_command = [sys.executable, str(TOKENISER), "--method", args.method, "--overwrite"]

    if args.method in KEYED_METHODS:
        if not args.key_file.is_file():
            sys.exit(
                f"No key at {args.key_file}. Create it once with:\n"
                f"  python src/hospital/token_generator.py --generate-key {DEFAULT_KEY.relative_to(ROOT)}"
            )
        token_command += ["--key-file", str(args.key_file)]

    if args.method != "hmac_sha256":
        out = ROOT / "src" / "data" / f"tokens_{args.method}"
        token_command += ["--output-dir", str(out), "--report-dir", str(out / "reports")]

    run("Step 1: cleaning", [sys.executable, str(CLEANING), "--overwrite"])
    run("Step 2: tokenisation", token_command)

    folder = "tokens" if args.method == "hmac_sha256" else f"tokens_{args.method}"
    print(f"\nDone. Token files are in src/data/{folder}/")


if __name__ == "__main__":
    main()

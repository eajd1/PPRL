"""Tests for token_generator.py.

Run from the repository root:

    python -m unittest discover -s src/hospital -p "test_*.py" -v
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import token_generator as tg  # noqa: E402

KEY = bytes.fromhex("11" * 32)
OTHER_KEY = bytes.fromhex("22" * 32)
COMBOS = ["first_last_fake_medicare_id", "first_last_middle_dob_dmy_phone"]


def write_combinations(path: Path, rows: list[dict[str, str]], header=None) -> None:
    header = header or ["hospital_id", "local_patient_id", *COMBOS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def row(hospital="1", pid="P1", medicare="lucy|yoder|9842414729",
        phone="lucy|yoder|portillo|23/08/1957|+61457216934") -> dict[str, str]:
    return {"hospital_id": hospital, "local_patient_id": pid, COMBOS[0]: medicare, COMBOS[1]: phone}


class MakeTokenTests(unittest.TestCase):

    def test_same_text_same_token(self):
        self.assertEqual(tg.make_token("c", "a|b", "hmac_sha256", KEY),
                        tg.make_token("c", "a|b", "hmac_sha256", KEY))

    def test_blank_stays_blank(self):
        self.assertEqual(tg.make_token("c", "", "hmac_sha256", KEY), "")

    def test_one_character_changes_token(self):
        self.assertNotEqual(tg.make_token("c", "a|b", "hmac_sha256", KEY),
                            tg.make_token("c", "a|c", "hmac_sha256", KEY))

    def test_combination_name_is_part_of_token(self):
        self.assertNotEqual(tg.make_token("c1", "a|b", "hmac_sha256", KEY),
                            tg.make_token("c2", "a|b", "hmac_sha256", KEY))

    def test_different_key_different_token(self):
        self.assertNotEqual(tg.make_token("c", "a|b", "hmac_sha256", KEY),
                            tg.make_token("c", "a|b", "hmac_sha256", OTHER_KEY))

    def test_method_lengths(self):
        for method, length in {"hmac_sha256": 64, "sha256": 64, "sha512": 128, "salted_sha256": 64}.items():
            key = KEY if method in tg.KEYED_METHODS else None
            self.assertEqual(len(tg.make_token("c", "a", method, key)), length, method)

    def test_unkeyed_sha256_can_be_recomputed_by_anyone(self):
        message = tg.FIELD_SEPARATOR.join((tg.TOKEN_SCHEME_VERSION, "c", "a|b"))
        self.assertEqual(tg.make_token("c", "a|b", "sha256", None),
                        hashlib.sha256(message.encode()).hexdigest())


class ReadCombinationsTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "hospital1_combinations.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def assert_rejected(self, rows, fragment, header=None):
        write_combinations(self.path, rows, header)
        with self.assertRaises(tg.TokenisationError) as ctx:
            tg.read_combinations(self.path)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def test_reads_columns_and_rows(self):
        write_combinations(self.path, [row()])
        columns, rows = tg.read_combinations(self.path)
        self.assertEqual(columns, COMBOS)
        self.assertEqual(len(rows), 1)

    def test_missing_id_column(self):
        self.assert_rejected([row()], "local_patient_id", ["hospital_id", *COMBOS])

    def test_duplicate_patient(self):
        self.assert_rejected([row(), row()], "repeats row 2")

    def test_blank_patient_id(self):
        self.assert_rejected([row(pid="")], "blank local_patient_id")

    def test_mixed_hospitals(self):
        self.assert_rejected([row(), row(hospital="2", pid="P2")], "exactly one")

    def test_errors_do_not_leak_values(self):
        message = self.assert_rejected([row(), row()], "repeats")
        self.assertNotIn("lucy", message)


class EndToEndTests(unittest.TestCase):

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tg.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_match_across_hospitals_and_no_raw_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp, out, rep = root / "combinations", root / "tokens", root / "reports"
            inp.mkdir()
            key_file = root / "k.key"
            tg.generate_key_file(key_file)
            write_combinations(inp / "hospital1_combinations.csv", [row(pid="0")])
            write_combinations(inp / "hospital2_combinations.csv",
                            [row(hospital="2", pid="57"),
                                row(hospital="2", pid="58", medicare="kel|ponce|2390228694", phone="")])

            code, _, err = self.run_main(["--input-dir", str(inp), "--output-dir", str(out),
                                        "--report-dir", str(rep), "--key-file", str(key_file)])
            self.assertEqual(code, 0, err)

            h1 = list(csv.DictReader((out / "hospital1_tokens.csv").open()))
            h2 = list(csv.DictReader((out / "hospital2_tokens.csv").open()))
            self.assertEqual(list(h1[0]), ["site_id", "local_patient_id", *COMBOS])
            self.assertEqual(h1[0]["local_patient_id"], "0")          # copied, not hashed
            for name in COMBOS:
                self.assertEqual(h1[0][name], h2[0][name])            # same patient matches
                self.assertNotEqual(h1[0][name], h2[1][name])         # different patient doesn't
            self.assertEqual(h2[1][COMBOS[1]], "")                    # blank stays blank

            text = (out / "hospital2_tokens.csv").read_text()
            for raw in ("lucy", "yoder", "9842414729", "+61457216934", "ponce"):
                self.assertNotIn(raw, text)

    def test_missing_key_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(tg, "DEFAULT_KEY_FILE", Path(tmp) / "none.key"), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tg.KEY_ENV_VAR, None)
            code, _, err = self.run_main(["--input-dir", tmp])
            self.assertEqual(code, 1)
            self.assertIn("No key found", err)

    def test_generate_key_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.key"
            tg.generate_key_file(path)
            self.assertEqual(len(tg.load_key(path)), tg.MIN_KEY_BYTES)
            with self.assertRaises(tg.TokenisationError):
                tg.generate_key_file(path)


if __name__ == "__main__":
    unittest.main()

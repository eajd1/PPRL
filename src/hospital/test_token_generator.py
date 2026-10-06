from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import token_generator as tg  # noqa: E402

KEY = bytes.fromhex("11" * 32)
OTHER_KEY = bytes.fromhex("22" * 32)

HEADER = [
    "hospital_id", "local_patient_id", "first_name", "last_name", "date_of_birth",
    "sex", "postcode", "phone", "fake_medicare_id",
]


def patient(**overrides: str) -> dict[str, str]:
    row = {
        "hospital_id": "1",
        "local_patient_id": "P001",
        "first_name": "linda",
        "last_name": "macdonald",
        "date_of_birth": "1999-02-07",
        "sex": "F",
        "postcode": "2475",
        "phone": "+61479802882",
        "fake_medicare_id": "2620051826",
    }
    row.update(overrides)
    return row


def write_linkage(path: Path, rows: list[dict[str, str]], header: list[str] = HEADER) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class TokenConstructionTests(unittest.TestCase):

    def test_same_patient_at_two_sites_gets_identical_tokens(self):
        a = tg.tokenise_row(patient(hospital_id="1", local_patient_id="A9"), "hmac_sha256", KEY)
        b = tg.tokenise_row(patient(hospital_id="2", local_patient_id="B4"), "hmac_sha256", KEY)
        self.assertEqual(a, b)
        self.assertTrue(all(a.values()))

    def test_one_character_change_changes_only_affected_tokens(self):
        base = tg.tokenise_row(patient(), "hmac_sha256", KEY)
        changed = tg.tokenise_row(patient(phone="+61479802883"), "hmac_sha256", KEY)
        self.assertNotEqual(base["token_4"], changed["token_4"])
        for name in ("token_1", "token_2", "token_3", "token_5"):
            self.assertEqual(base[name], changed[name])

    def test_missing_field_blanks_only_tokens_that_use_it(self):
        tokens = tg.tokenise_row(patient(phone=""), "hmac_sha256", KEY)
        self.assertEqual(tokens["token_4"], "")
        self.assertTrue(tokens["token_5"])

    def test_missing_dob_blanks_every_token(self):
        tokens = tg.tokenise_row(patient(date_of_birth=""), "hmac_sha256", KEY)
        self.assertEqual(set(tokens.values()), {""})

    def test_two_patients_missing_phone_do_not_share_token_4(self):
        a = tg.tokenise_row(patient(phone=""), "hmac_sha256", KEY)
        b = tg.tokenise_row(patient(phone="", first_name="kellen"), "hmac_sha256", KEY)
        self.assertEqual(a["token_4"], "")
        self.assertEqual(b["token_4"], "")

    def test_unknown_sex_is_treated_as_missing(self):
        tokens = tg.tokenise_row(patient(sex="U"), "hmac_sha256", KEY)
        self.assertEqual(tokens["token_1"], "")
        self.assertTrue(tokens["token_2"])

    def test_first_initial_is_derived(self):
        a = tg.tokenise_row(patient(first_name="linda"), "hmac_sha256", KEY)
        b = tg.tokenise_row(patient(first_name="lyn"), "hmac_sha256", KEY)
        self.assertEqual(a["token_3"], b["token_3"])
        self.assertNotEqual(a["token_1"], b["token_1"])

    def test_field_boundaries_are_unambiguous(self):
        a = tg.linkage_values(patient(first_name="ann", last_name="abel"))
        b = tg.linkage_values(patient(first_name="anna", last_name="bel"))
        self.assertNotEqual(tg.build_message("token_1", a), tg.build_message("token_1", b))

    def test_token_name_separates_rules_with_same_values(self):
        values = tg.linkage_values(patient())
        self.assertIn("token_4", tg.build_message("token_4", values))
        self.assertNotEqual(
            tg.build_message("token_4", values),
            tg.build_message("token_5", dict(values, fake_medicare_id=values["phone"])),
        )

    def test_different_key_gives_different_tokens(self):
        a = tg.tokenise_row(patient(), "hmac_sha256", KEY)
        b = tg.tokenise_row(patient(), "hmac_sha256", OTHER_KEY)
        for name in tg.TOKEN_RULES:
            self.assertNotEqual(a[name], b[name])

    def test_tokens_are_deterministic(self):
        self.assertEqual(
            tg.tokenise_row(patient(), "hmac_sha256", KEY),
            tg.tokenise_row(patient(), "hmac_sha256", KEY),
        )

    def test_method_output_lengths(self):
        lengths = {"hmac_sha256": 64, "sha256": 64, "sha512": 128, "salted_sha256": 64}
        for method, length in lengths.items():
            key = KEY if method in tg.KEYED_METHODS else None
            token = tg.tokenise_row(patient(), method, key)["token_1"]
            self.assertEqual(len(token), length, method)

    def test_unkeyed_sha256_is_reproducible_without_secret(self):
        # Demonstrates the dictionary-attack weakness: anyone can recompute it.
        message = tg.build_message("token_4", tg.linkage_values(patient()))
        import hashlib
        expected = hashlib.sha256(message.encode()).hexdigest()
        self.assertEqual(tg.tokenise_row(patient(), "sha256", None)["token_4"], expected)


class ValidationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_rejected(self, rows, fragment, header=HEADER):
        path = self.dir / "hospital1_linkage_patients.csv"
        write_linkage(path, rows, header)
        with self.assertRaises(tg.TokenisationError) as ctx:
            tg.read_linkage_file(path)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def test_rejects_uncleaned_dob(self):
        self.assert_rejected([patient(date_of_birth="7-02-1999")], "date_of_birth")

    def test_rejects_impossible_dob(self):
        self.assert_rejected([patient(date_of_birth="1999-02-30")], "date_of_birth")

    def test_rejects_uncleaned_name_phone_postcode_sex(self):
        message = self.assert_rejected(
            [patient(first_name="Linda", phone="0479802882", postcode="475", sex="female")],
            "failed validation",
        )
        for field in ("first_name", "phone", "postcode", "sex"):
            self.assertIn(field, message)

    def test_error_messages_do_not_leak_values(self):
        message = self.assert_rejected([patient(first_name="Linda")], "first_name")
        self.assertNotIn("Linda", message)

    def test_rejects_duplicate_local_patient_id(self):
        self.assert_rejected([patient(), patient(first_name="kellen")], "duplicates row 2")

    def test_rejects_blank_local_patient_id(self):
        self.assert_rejected([patient(local_patient_id="")], "blank local_patient_id")

    def test_rejects_mixed_hospital_ids(self):
        self.assert_rejected(
            [patient(), patient(hospital_id="2", local_patient_id="P002")],
            "exactly one non-blank hospital_id",
        )

    def test_rejects_missing_columns(self):
        header = [c for c in HEADER if c != "fake_medicare_id"]
        self.assert_rejected([patient()], "fake_medicare_id", header)

    def test_accepts_blank_optional_values(self):
        path = self.dir / "hospital1_linkage_patients.csv"
        write_linkage(path, [patient(phone="", postcode="", first_name="")])
        self.assertEqual(len(tg.read_linkage_file(path)), 1)


class KeyTests(unittest.TestCase):

    def test_short_key_rejected(self):
        with self.assertRaises(tg.TokenisationError):
            tg.parse_key("ab" * 16, "test")

    def test_non_hex_key_rejected(self):
        with self.assertRaises(tg.TokenisationError):
            tg.parse_key("not-hex" * 10, "test")

    def test_generate_key_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.key"
            tg.generate_key_file(path)
            self.assertEqual(len(tg.load_key(path)), tg.MIN_KEY_BYTES)
            with self.assertRaises(tg.TokenisationError):
                tg.generate_key_file(path)


class EndToEndTests(unittest.TestCase):

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = tg.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_full_run_matches_across_sites_and_leaks_no_raw_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp, out, rep = root / "in", root / "tokens", root / "reports"
            inp.mkdir()
            key_file = root / "k.key"
            tg.generate_key_file(key_file)

            shared = patient()
            write_linkage(inp / "hospital1_linkage_patients.csv",
                        [dict(shared, hospital_id="1", local_patient_id="A1")])
            write_linkage(inp / "hospital2_linkage_patients.csv",
                        [dict(shared, hospital_id="2", local_patient_id="B7"),
                        patient(hospital_id="2", local_patient_id="B8", first_name="kellen",
                                last_name="ponce", date_of_birth="2002-09-27", phone="",
                                fake_medicare_id="2390228694")])

            args = ["--input-dir", str(inp), "--output-dir", str(out),
                    "--report-dir", str(rep), "--key-file", str(key_file)]
            code, stdout, stderr = self.run_main(args)
            self.assertEqual(code, 0, stderr)

            with (out / "hospital1_tokens.csv").open() as f:
                h1 = list(csv.DictReader(f))
            with (out / "hospital2_tokens.csv").open() as f:
                h2 = list(csv.DictReader(f))

            self.assertEqual(list(h1[0]), ["site_id", "local_patient_id", *tg.TOKEN_RULES])
            for name in tg.TOKEN_RULES:
                self.assertEqual(h1[0][name], h2[0][name])
                self.assertNotEqual(h1[0][name], h2[1][name])
            self.assertEqual(h2[1]["token_4"], "")

            text = (out / "hospital2_tokens.csv").read_text()
            for raw in ("linda", "macdonald", "1999-02-07", "+61479802882",
                        "2620051826", "kellen", "2475"):
                self.assertNotIn(raw, text)

            report = json.loads((rep / "hospital2_token_report.json").read_text())
            self.assertEqual(report["patients"], 2)
            self.assertEqual(report["tokens_blank_due_to_missing_fields"]["token_4"], 1)

            # Second run without --overwrite must refuse.
            code, _, stderr = self.run_main(args)
            self.assertEqual(code, 1)
            self.assertIn("--overwrite", stderr)

    def test_hmac_without_key_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, stderr = self.run_main(["--input-dir", tmp])
            self.assertEqual(code, 1)
            self.assertIn("No key supplied", stderr)


if __name__ == "__main__":
    unittest.main()

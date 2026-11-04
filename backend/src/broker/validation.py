import csv
import io
import re

TOKEN_COLUMNS = [f"token{i}" for i in range(1, 26)]
COLUMNS = ["site_id", "local_patient_id", "token_version", "key_id", "method", *TOKEN_COLUMNS]


class ValidationError(ValueError):
    pass


def validate_csv(data: bytes, site_id: str, settings):
    if not data or len(data) > settings.max_bytes:
        raise ValidationError("File is empty or exceeds the upload limit")
    try:
        text = data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader, [])
        if len(headers) != len(COLUMNS) or set(headers) != set(COLUMNS):
            raise ValidationError("Expected 30 CSV columns: site_id, local_patient_id, token_version, key_id, method and token1 through token25; no duplicate or unexpected headers. Regenerate older files before sending.")
        records, duplicates = {}, 0
        for row_number, cells in enumerate(reader, 2):
            if row_number - 1 > settings.max_rows:
                raise ValidationError("Submission exceeds the record limit")
            if len(cells) != len(headers):
                raise ValidationError(f"Row {row_number}: incorrect field count")
            row = dict(zip(headers, cells))
            if row["site_id"] != site_id:
                raise ValidationError(f"Row {row_number}: site_id does not match the submitting hospital")
            pid = row["local_patient_id"]
            if not pid.strip() or len(pid) > 200 or any(ord(c) < 32 for c in pid):
                raise ValidationError(f"Row {row_number}: invalid local_patient_id")
            if row["token_version"] != settings.token_version or row["key_id"] not in settings.key_ids or row["method"] != "hmac_sha256":
                raise ValidationError(f"Row {row_number}: incompatible token_version, key_id or method")
            for name in TOKEN_COLUMNS:
                if row[name] and not re.fullmatch(r"[0-9a-f]{64}", row[name]):
                    raise ValidationError(f"Row {row_number}: invalid {name}")
            if pid in records:
                if records[pid] != row:
                    raise ValidationError(f"Row {row_number}: conflicting duplicate local_patient_id")
                duplicates += 1
            else:
                records[pid] = row
        if not records:
            raise ValidationError("CSV must contain at least one record")
        return [records[k] for k in sorted(records)], duplicates
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValidationError("Invalid UTF-8 CSV structure") from exc

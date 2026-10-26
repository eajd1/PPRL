"""Generate coordinated local-demo credentials; never print secrets or overwrite files."""
import hashlib
import json
import secrets
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    names = [".env", ".env.broker", ".env.postgres", ".env.admin"]
    if any((root / name).exists() for name in names):
        raise SystemExit("Configuration already exists. Preserve it and configure broker settings using the examples and BROKER.md.")
    password, admin = secrets.token_hex(24), secrets.token_urlsafe(32)
    credentials = {s: secrets.token_urlsafe(32) for s in ("1", "2", "3")}
    hashes = {s: hashlib.sha256(value.encode()).hexdigest() for s, value in credentials.items()}
    files = {
        ".env": f"PPRL_TOKEN_KEY={secrets.token_hex(32)}\nPPRL_KEY_ID=pprl-linkage-2026-01\nPPRL_TOKEN_VERSION=v3\nPPRL_BROKER_URL=http://broker:8000\nPPRL_BROKER_CREDENTIALS='{json.dumps(credentials)}'\n",
        ".env.broker": f"BROKER_DATABASE_URL=postgresql+psycopg://pprl:{password}@postgres:5432/pprl\nBROKER_ADMIN_TOKEN_HASH={hashlib.sha256(admin.encode()).hexdigest()}\nBROKER_HOSPITAL_TOKEN_HASHES='{json.dumps(hashes)}'\nBROKER_KEY_IDS=pprl-linkage-2026-01\n",
        ".env.postgres": f"POSTGRES_DB=pprl\nPOSTGRES_USER=pprl\nPOSTGRES_PASSWORD={password}\n",
        ".env.admin": f"BROKER_ADMIN_TOKEN={admin}\n",
    }
    for name, content in files.items():
        with (root / name).open("x", encoding="utf-8") as handle:
            handle.write(content)
    print("Created local-demo configuration. The dashboard credential is in .env.admin. No credentials were printed.")


if __name__ == "__main__":
    main()

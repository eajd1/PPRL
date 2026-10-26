"""Broker configuration deliberately never loads the hospital .env."""
import hashlib
import json
import os
from dataclasses import dataclass, field


def credential_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("BROKER_DATABASE_URL", ""))
    admin_hash: str = field(default_factory=lambda: os.environ.get("BROKER_ADMIN_TOKEN_HASH", ""))
    hospitals: dict = field(default_factory=lambda: json.loads(os.environ.get("BROKER_HOSPITAL_TOKEN_HASHES", "{}")))
    key_ids: tuple = field(default_factory=lambda: tuple(os.environ.get("BROKER_KEY_IDS", "pprl-linkage-2026-01").split(",")))
    token_version: str = "v3"
    max_bytes: int = field(default_factory=lambda: int(os.environ.get("BROKER_MAX_BYTES", 25 * 1024 * 1024)))
    max_rows: int = field(default_factory=lambda: int(os.environ.get("BROKER_MAX_ROWS", 10000)))
    static_dir: str = field(default_factory=lambda: os.environ.get("BROKER_STATIC_DIR", "/app/dashboard"))

    def validate(self):
        import re
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Set BROKER_DATABASE_URL to a PostgreSQL psycopg URL")
        hashes = [self.admin_hash, *self.hospitals.values()]
        if not self.hospitals or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes):
            raise ValueError("Configure administrator and hospital credential hashes")
        if len(set(hashes)) != len(hashes):
            raise ValueError("Every service and administrator must have a distinct credential")
        if any(not isinstance(k, str) or not k.strip() or len(k) > 120 for k in self.hospitals):
            raise ValueError("Invalid configured hospital identity")

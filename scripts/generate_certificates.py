"""Generate isolated LAN demo credentials using OpenSSL (no Python dependencies).

Run on a trusted provisioning machine. Distribute only each machine's bundle.
Never distribute certs/ca/ca.key or copy hospital .env files to the broker.
"""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


def run(*args, input=None):
    result = subprocess.run(["openssl", *map(str, args)], input=input, text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


def generate(destination: Path, hosts: list[str], password: str):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Refusing to overwrite existing credentials; choose an empty output directory")
    if len(password) < 12 or "\n" in password or "\r" in password:
        raise ValueError("Operator password must be at least 12 characters and a single line")
    sans = []
    for host in hosts:
        try:
            sans.append(f"IP:{ipaddress.ip_address(host)}")
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
                raise ValueError("Invalid SAN hostname")
            sans.append(f"DNS:{host}")
    destination.mkdir(parents=True, exist_ok=True)
    ca = destination / "ca"
    ca.mkdir()
    config = ca / "openssl.cnf"
    config.write_text("[req]\ndistinguished_name=dn\n[dn]\n", encoding="ascii")

    def key(path):
        run("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", path)
        path.chmod(0o600)

    key(ca / "ca.key")
    run("req", "-config", config, "-x509", "-new", "-key", ca / "ca.key", "-sha256", "-days", "365",
        "-subj", "/CN=PPRL LAN Demo CA", "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-out", ca / "ca.crt")
    allowlist = {}
    for identity in ["broker", "hospital1", "hospital2", "hospital3"]:
        bundle = destination / identity
        bundle.mkdir()
        stem = "broker" if identity == "broker" else "client"
        key(bundle / f"{stem}.key")
        run("req", "-config", config, "-new", "-key", bundle / f"{stem}.key", "-subj", f"/CN={identity}", "-out", bundle / "request.csr")
        extension = bundle / "extensions.cnf"
        extension.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=" +
                             ("serverAuth\nsubjectAltName=" + ",".join(sans) if identity == "broker" else "clientAuth") + "\n", encoding="ascii")
        run("x509", "-req", "-in", bundle / "request.csr", "-CA", ca / "ca.crt", "-CAkey", ca / "ca.key",
            "-set_serial", "0x" + uuid.uuid4().hex, "-days", "90", "-sha256", "-extfile", extension,
            "-out", bundle / f"{stem}.crt")
        shutil.copyfile(ca / "ca.crt", bundle / "ca.crt")
        if identity != "broker":
            fingerprint = run("x509", "-in", bundle / "client.crt", "-noout", "-fingerprint", "-sha256")
            allowlist[fingerprint.split("=", 1)[1].replace(":", "").lower()] = identity
        (bundle / "request.csr").unlink()
        extension.unlink()
    (destination / "broker" / "allowlist.json").write_text(json.dumps(allowlist, indent=2), encoding="utf-8")
    # OpenSSL reads the password from stdin; it never appears in a process argument.
    password_hash = run("passwd", "-6", "-stdin", input=password + "\n")
    (destination / "broker" / "operator.htpasswd").write_text("operator:" + password_hash + "\n", encoding="ascii")
    print(f"Generated credentials in {destination}. Operator username: operator. Certificates expire after 90 days.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("certs"))
    parser.add_argument("--broker-host", action="append", required=True, help="Repeat for each DNS name or LAN IP SAN")
    parser.add_argument("--password-env", help="Read operator password from this environment variable (for automation)")
    args = parser.parse_args()
    generate(args.output, args.broker_host,
             os.environ[args.password_env] if args.password_env else getpass.getpass("Operator password (12+ characters): "))

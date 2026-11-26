"""Publish signed device-key snapshots for local TMC demos."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from packages.contracts.device_key_snapshot import canonical_snapshot
from tools import fake_mobile

SIGNING_KID = "demo-root"


def _load_or_create_signer(path: Path) -> ec.EllipticCurvePrivateKey:
    try:
        pem = path.read_bytes()
    except FileNotFoundError:
        key = ec.generate_private_key(ec.SECP256R1())
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        return key

    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError("The demo signing key must be an ECDSA P-256 private key")
    return key


def _write_root_public_key(path: Path, key: ec.EllipticCurvePublicKey) -> None:
    pem = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as file:
            file.write(pem)
    except FileExistsError:
        pinned = serialization.load_pem_public_key(path.read_bytes())
        if not isinstance(pinned, ec.EllipticCurvePublicKey) or (
            pinned.public_numbers() != key.public_numbers()
        ):
            raise ValueError(f"Existing trust anchor {path} differs from the signing key")


def _write_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary_path = Path(file.name)
    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device", nargs=2, metavar=("DID", "EMPLOYEE_REF"), action="append", required=True
    )
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--issued-at", help="UTC ISO 8601 timestamp, defaults to current UTC")
    parser.add_argument("--revoke-device", metavar="DID", action="append", default=[])
    parser.add_argument("--revoke-employee", metavar="EMPLOYEE_REF", action="append", default=[])
    parser.add_argument("--signing-key", type=Path, default=Path("runtime-files/demo_root.pem"))
    parser.add_argument(
        "--root-public-key", type=Path, default=Path("runtime-files/pinned_root_pub.pem")
    )
    parser.add_argument("--output", type=Path, default=Path("snapshot.json"))
    args = parser.parse_args(argv)

    try:
        if not 0 < args.version <= 2**63 - 1:
            raise ValueError("version must be a positive SQLite integer")
        issued_at = (
            datetime.fromisoformat(args.issued_at)
            if args.issued_at is not None
            else datetime.now(timezone.utc)
        )
        if issued_at.utcoffset() != timedelta(0):
            raise ValueError("--issued-at must use UTC")
        devices = dict(args.device)
        if len(devices) != len(args.device):
            raise ValueError("Each DID may appear only once")
        if any(not ref.strip() for ref in [*devices.values(), *args.revoke_employee]):
            raise ValueError("Employee references must be nonempty")
        revoked_devices = set(args.revoke_device)
        if revoked_devices - devices.keys():
            raise ValueError("Each revoked DID must also be included with --device")

        device_keys = {fake_mobile.private_key_path(did).resolve() for did in devices}
        demo_paths = {path.resolve() for path in (args.signing_key, args.root_public_key, args.output)}
        if len(demo_paths) != 3 or device_keys & demo_paths:
            raise ValueError("Snapshot, root key, and device keys must use separate paths")

        payload = {
            "version": args.version,
            "issued_at": issued_at.isoformat(),
            "signing_kid": SIGNING_KID,
            "devices": [
                {
                    "did": did,
                    "employee_ref": ref,
                    "public_key_pem": fake_mobile.export_public_key(did, as_pem=True).decode("ascii"),
                    "status": "REVOKED" if did in revoked_devices else "ACTIVE",
                }
                for did, ref in devices.items()
            ],
            "revoked_employees": sorted(set(args.revoke_employee)),
        }
        signer = _load_or_create_signer(args.signing_key)
        _write_root_public_key(args.root_public_key, signer.public_key())
        signature = signer.sign(canonical_snapshot(payload), ec.ECDSA(hashes.SHA256()))
        payload["sig"] = base64.b64encode(signature).decode("ascii")
        _write_snapshot(args.output, payload)
    except (ValueError, TypeError, OSError, UnsupportedAlgorithm) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fake mobile device QR generator for TMC attendance testing."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


DEVICES_DIR = Path(__file__).resolve().parent / "devices"
DID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def validate_did(did: str) -> str:
    """Validate a device ID so it cannot escape the devices directory."""
    if not DID_PATTERN.fullmatch(did):
        raise ValueError(
            "did may contain only letters, digits, dots, underscores, and hyphens "
            "and must start with a letter or digit"
        )
    return did


def private_key_path(did: str) -> Path:
    validate_did(did)
    return DEVICES_DIR / f"{did}.pem"


def state_path(did: str) -> Path:
    validate_did(did)
    return DEVICES_DIR / f"{did}.state.json"


def load_or_create_private_key(did: str) -> ec.EllipticCurvePrivateKey:
    """Load a P-256 private key, or create and persist it if absent."""
    validate_did(did)
    DEVICES_DIR.mkdir(parents=True, exist_ok=True)
    path = private_key_path(did)

    if path.exists():
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot load private key at {path}: {exc}") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError(f"{path} is not an Elliptic Curve private key")
        if not isinstance(key.curve, ec.SECP256R1):
            raise ValueError(f"{path} is not a P-256 (SECP256R1) private key")
        return key

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        path.write_bytes(pem)
    except OSError as exc:
        raise OSError(f"Cannot write private key to {path}: {exc}") from exc
    return key


def export_public_key(
    did: str,
    *,
    private_key: Optional[ec.EllipticCurvePrivateKey] = None,
    as_pem: bool = False,
) -> bytes:
    """Return a P-256 public key as PEM, or as DER if as_pem is false."""
    if private_key is None:
        private_key = load_or_create_private_key(did)
    public_key = private_key.public_key()
    if as_pem:
        return public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def canonical_payload(v: int, did: str, iat: int, seq: int) -> bytes:
    """Build the exact canonical byte string signed by the mobile."""
    return f"{v}|{did}|{iat}|{seq}".encode("utf-8")


def sign_payload(
    private_key: ec.EllipticCurvePrivateKey,
    v: int,
    did: str,
    iat: int,
    seq: int,
) -> bytes:
    """Sign canonical payload bytes with ECDSA SHA-256 and return DER bytes."""
    return private_key.sign(
        canonical_payload(v, did, iat, seq),
        ec.ECDSA(hashes.SHA256()),
    )


def encode_qr_payload(payload: dict[str, Any]) -> str:
    """Encode a payload JSON object as standard Base64."""
    json_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.b64encode(json_bytes).decode("ascii")


def decode_qr_payload(encoded: str) -> dict[str, Any]:
    """Decode and validate the JSON structure of a Base64 QR payload."""
    try:
        raw = base64.b64decode(encoded, validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (base64.binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid QR payload encoding: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("QR payload must be a JSON object")
    return payload


def verify_signature(
    public_key: ec.EllipticCurvePublicKey,
    payload: dict[str, Any],
) -> bool:
    """Verify a payload signature for testing and diagnostics."""
    try:
        sig = base64.b64decode(payload["sig"], validate=True)
        canonical = canonical_payload(
            int(payload["v"]),
            str(payload["did"]),
            int(payload["iat"]),
            int(payload["seq"]),
        )
        public_key.verify(sig, canonical, ec.ECDSA(hashes.SHA256()))
        return True
    except (KeyError, TypeError, ValueError, InvalidSignature, base64.binascii.Error):
        return False


def read_state(did: str) -> dict[str, Any]:
    path = state_path(did)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cannot parse QR state at {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError(f"QR state at {path} must be a JSON object")
    return state


def write_state(did: str, qr: str, payload: dict[str, Any], seq: int) -> None:
    DEVICES_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "did": did,
        "seq": seq,
        "payload": payload,
        "qr": qr,
    }
    state_path(did).write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def tamper_signature(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with a validly Base64-encoded but invalid signature."""
    sig = base64.b64decode(str(payload["sig"]), validate=True)
    changed = bytearray(sig)
    if changed:
        changed[-1] ^= 1
    else:
        changed.append(1)
    tampered = dict(payload)
    tampered["sig"] = base64.b64encode(bytes(changed)).decode("ascii")
    return tampered


def generate_qr(
    did: str,
    *,
    iat: Optional[int] = None,
    seq: Optional[int] = None,
    resend: bool = False,
    tamper_sig: bool = False,
    private_key: Optional[ec.EllipticCurvePrivateKey] = None,
) -> tuple[str, dict[str, Any]]:
    """Generate or resend a QR and return (Base64 QR, JSON payload)."""
    validate_did(did)
    state = read_state(did)

    if resend:
        if "qr" not in state or "payload" not in state:
            raise ValueError(
                f"No previously generated QR found for did={did}. "
                "Generate a QR first."
            )
        payload = dict(state["payload"])
        qr = str(state["qr"])
        if tamper_sig:
            payload = tamper_signature(payload)
            qr = encode_qr_payload(payload)
        return qr, payload

    if private_key is None:
        private_key = load_or_create_private_key(did)

    timestamp = int(time.time()) if iat is None else iat
    if timestamp < 0:
        raise ValueError("iat must be a non-negative Unix timestamp")

    previous_seq = state.get("seq", 0)
    if seq is None:
        sequence = int(previous_seq) + 1
    else:
        if seq < 0:
            raise ValueError("seq must be a non-negative integer")
        sequence = seq

    payload: dict[str, Any] = {
        "v": 1,
        "did": did,
        "iat": timestamp,
        "seq": sequence,
    }
    signature = sign_payload(private_key, 1, did, timestamp, sequence)
    payload["sig"] = base64.b64encode(signature).decode("ascii")

    if tamper_sig:
        payload = tamper_signature(payload)

    qr = encode_qr_payload(payload)
    state_seq = max(sequence, int(previous_seq))
    write_state(did, qr, payload, state_seq)
    return qr, payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate test QR payloads for a fake TMC mobile device"
    )
    parser.add_argument("--did", default="device_001", help="Device ID")
    parser.add_argument("--iat", type=int, help="Custom Unix timestamp")
    parser.add_argument("--seq", type=int, help="Explicit sequence number")
    parser.add_argument(
        "--resend",
        action="store_true",
        help="Resend the exact QR payload generated for this did",
    )
    parser.add_argument(
        "--tamper-sig",
        action="store_true",
        help="Tamper the signature while keeping other payload fields unchanged",
    )
    parser.add_argument(
        "--export-public-key",
        "--public-key",
        action="store_true",
        help="Print the did public key in PEM format instead of generating a QR",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.resend and args.iat is not None:
            raise ValueError("--resend cannot be combined with --iat")
        if args.resend and args.seq is not None:
            raise ValueError("--resend cannot be combined with --seq")
        validate_did(args.did)

        if args.export_public_key:
            private_key = load_or_create_private_key(args.did)
            sys.stdout.buffer.write(
                export_public_key(args.did, private_key=private_key, as_pem=True)
            )
            return 0

        qr, _payload = generate_qr(
            args.did,
            iat=args.iat,
            seq=args.seq,
            resend=args.resend,
            tamper_sig=args.tamper_sig,
        )
        print(qr)
        return 0
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

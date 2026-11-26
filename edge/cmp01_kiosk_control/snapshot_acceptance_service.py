"""Verify signed CMP-02 snapshots before they reach the cache."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from packages.contracts.device_key_snapshot import canonical_snapshot

from .ports import DeviceEntry, DeviceKeySnapshot


class SnapshotAcceptanceService:
    def __init__(self, pinned_root_pub_path: str | Path) -> None:
        self._root_key = _p256_public_key(Path(pinned_root_pub_path).read_bytes())

    def verify(self, raw: bytes) -> DeviceKeySnapshot | None:
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(payload, dict) or set(payload) != {
                "version", "issued_at", "signing_kid", "devices", "revoked_employees", "sig",
            }:
                return None
            signature = base64.b64decode(_required_text(payload["sig"]), validate=True)
            self._root_key.verify(signature, canonical_snapshot(payload), ec.ECDSA(hashes.SHA256()))

            version = payload["version"]
            issued_at = datetime.fromisoformat(_required_text(payload["issued_at"]))
            if type(version) is not int or not 1 <= version < 2**63:
                return None
            if issued_at.utcoffset() != timedelta(0):
                return None
            if not isinstance(payload["devices"], list) or not isinstance(payload["revoked_employees"], list):
                return None
            devices = tuple(_device(entry) for entry in payload["devices"])
            if len({entry.did for entry in devices}) != len(devices):
                return None
            return DeviceKeySnapshot(
                version=version,
                issued_at=issued_at,
                signing_kid=_required_text(payload["signing_kid"]),
                devices=devices,
                revoked_employees=frozenset(_required_text(ref) for ref in payload["revoked_employees"]),
                sig=signature,
            )
        except (InvalidSignature, UnsupportedAlgorithm, ValueError, TypeError, RecursionError):
            return None


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Snapshot JSON contains duplicate fields")
    return result


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Snapshot field must be a non-empty string")
    return value


def _p256_public_key(pem: bytes) -> ec.EllipticCurvePublicKey:
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Snapshot keys must be P-256 public keys")
    return key


def _device(value: object) -> DeviceEntry:
    if not isinstance(value, dict) or set(value) != {"did", "employee_ref", "public_key_pem", "status"}:
        raise ValueError("Invalid snapshot device fields")
    public_key_pem = _required_text(value["public_key_pem"])
    _p256_public_key(public_key_pem.encode("utf-8"))
    status = _required_text(value["status"])
    if status not in {"ACTIVE", "REVOKED"}:
        raise ValueError("Invalid snapshot device status")
    return DeviceEntry(
        did=_required_text(value["did"]),
        employee_ref=_required_text(value["employee_ref"]),
        public_key_pem=public_key_pem,
        status=status,
    )

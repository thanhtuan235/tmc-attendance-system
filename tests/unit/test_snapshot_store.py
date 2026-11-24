from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from edge.cmp01_kiosk_control.adapters.snapshot_store import SnapshotStore
from packages.contracts.device_key_snapshot import canonical_snapshot


def test_store_rejects_tampering_and_rollback_and_restores_snapshot(tmp_path):
    signing_key = ec.generate_private_key(ec.SECP256R1())
    public_key = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pin = tmp_path / "root_pub.pem"
    pin.write_bytes(public_key)
    database = tmp_path / "edge_cache.db"
    store = SnapshotStore(database, pin)
    assert store.current() is None

    snapshot = {
        "version": 2,
        "issued_at": "2026-11-20T00:00:00Z",
        "signing_kid": "test-root",
        "devices": [
            {"did": "d1", "employee_ref": "e1", "public_key_pem": public_key.decode(), "status": "ACTIVE"},
            {"did": "d2", "employee_ref": "e2", "public_key_pem": public_key.decode(), "status": "REVOKED"},
            {"did": "d3", "employee_ref": "e3", "public_key_pem": public_key.decode(), "status": "ACTIVE"},
        ],
        "revoked_employees": ["e1"],
    }

    def signed(version):
        payload = {**snapshot, "version": version}
        signature = signing_key.sign(canonical_snapshot(payload), ec.ECDSA(hashes.SHA256()))
        return {**payload, "sig": base64.b64encode(signature).decode("ascii")}

    current = json.dumps(signed(2)).encode()
    assert store.load(current)
    assert not store.load(current)
    assert not store.load(json.dumps(signed(1)).encode())
    assert not store.load(json.dumps({**signed(2), "version": 3}).encode())
    assert not store.load(json.dumps(signed(2**63)).encode())
    assert not store.load(b'{"version":2,"version":3}')

    reopened = SnapshotStore(database, pin)
    assert reopened.current().version == 2
    assert reopened.lookup("d1").employee_ref == "e1"
    assert reopened.is_revoked("d1")
    assert reopened.is_revoked("d2")
    assert not reopened.is_revoked("d3")
    assert reopened.lookup("missing") is None
    assert not reopened.is_revoked("missing")

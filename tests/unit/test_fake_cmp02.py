import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from packages.contracts.device_key_snapshot import canonical_snapshot
from tools import fake_cmp02, fake_mobile


def test_publisher_signs_snapshots_and_keeps_the_same_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fake_mobile, "DEVICES_DIR", tmp_path / "devices")
    args = [
        "--device", "device_001", "EMP-01",
        "--revoke-device", "device_001",
        "--revoke-employee", "EMP-02",
        "--issued-at", "2026-11-20T00:00:00Z",
    ]

    for version in (1, 2):
        assert fake_cmp02.main([*args, "--version", str(version)]) == 0
        payload = json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))
        if version == 1:
            pin = (tmp_path / "runtime-files/pinned_root_pub.pem").read_bytes()
            root = serialization.load_pem_public_key(pin)
        root.verify(
            base64.b64decode(payload["sig"], validate=True),
            canonical_snapshot(payload),
            ec.ECDSA(hashes.SHA256()),
        )
        assert payload["version"] == version
        assert payload["issued_at"] == "2026-11-20T00:00:00+00:00"
        assert payload["revoked_employees"] == ["EMP-02"]
        assert payload["devices"] == [{
            "did": "device_001",
            "employee_ref": "EMP-01",
            "public_key_pem": fake_mobile.export_public_key("device_001", as_pem=True).decode("ascii"),
            "status": "REVOKED",
        }]

    assert (tmp_path / "runtime-files/pinned_root_pub.pem").read_bytes() == pin

    assert fake_cmp02.main([*args, "--version", "3", "--signing-key", "other.pem"]) == 2
    assert (tmp_path / "runtime-files/pinned_root_pub.pem").read_bytes() == pin
    assert json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))["version"] == 2

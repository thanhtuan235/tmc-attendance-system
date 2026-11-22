import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from tools.fake_mobile import (
    decode_qr_payload,
    generate_qr,
    load_or_create_private_key,
    verify_signature,
)


def test_generate_verify_and_reuse_key(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.fake_mobile.DEVICES_DIR",
        tmp_path,
    )
    key1 = load_or_create_private_key("device_test")
    key2 = load_or_create_private_key("device_test")

    assert key1.private_numbers() == key2.private_numbers()
    qr, payload = generate_qr("device_test", iat=1_700_000_000)

    assert set(payload) == {"v", "did", "iat", "seq", "sig"}
    assert payload["iat"] == 1_700_000_000
    assert decode_qr_payload(qr) == payload
    assert verify_signature(key1.public_key(), payload)


def test_resend_seq_and_tamper_modes(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.fake_mobile.DEVICES_DIR", tmp_path)

    first_qr, first_payload = generate_qr("device_replay", iat=100)
    resend_qr, resend_payload = generate_qr(
        "device_replay",
        resend=True,
    )
    assert resend_qr == first_qr
    assert resend_payload == first_payload

    old_qr, old_payload = generate_qr("device_replay", iat=100, seq=1)
    assert old_payload["seq"] == 1
    assert old_qr != first_qr

    tampered_qr, tampered_payload = generate_qr(
        "device_replay",
        iat=100,
        seq=2,
        tamper_sig=True,
    )
    assert decode_qr_payload(tampered_qr) == tampered_payload

    key = load_or_create_private_key("device_replay")
    assert not verify_signature(key.public_key(), tampered_payload)


def test_export_public_key_cli(tmp_path):
    key_path = Path("tools/devices/device_cli_test.pem")
    key_path.unlink(missing_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.fake_mobile",
            "--did",
            "device_cli_test",
            "--export-public-key",
        ],
        check=False,
        capture_output=True,
        text=False,
    )
    assert result.returncode == 0
    serialization.load_pem_public_key(result.stdout)
    key_path.unlink(missing_ok=True)

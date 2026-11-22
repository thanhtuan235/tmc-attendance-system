import base64
import dataclasses
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from edge.cmp01_kiosk_control.adapters.device_bound_qr_verifier import (
    DeviceBoundQrVerifier,
)
from edge.cmp01_kiosk_control.adapters.qr_verifier_selector import (
    PortalTokenVerifier,
    QrVerifierSelector,
)
from edge.cmp01_kiosk_control.ports import (
    DeviceEntry,
    DeviceKeySnapshot,
    QrPayload,
    ReasonCode,
)


@pytest.fixture
def private_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def snapshot(private_key):
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return DeviceKeySnapshot(
        version=1,
        issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        signing_kid="cmp02-test",
        devices=(DeviceEntry(
            did="device_test",
            employee_ref="employee_test",
            public_key_pem=public_pem,
        ),),
        revoked_employees=frozenset(),
        sig=b"snapshot-signature",
    )


def encode_payload(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw)


def signed_payload(private_key, now, did="device_test", iat=None, seq=1, v=1):
    iat = int(now.timestamp()) if iat is None else iat
    canonical = f"{v}|{did}|{iat}|{seq}".encode("utf-8")
    signature = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    return {
        "v": v,
        "did": did,
        "iat": iat,
        "seq": seq,
        "sig": base64.b64encode(signature).decode("ascii"),
    }


def test_valid_signature_is_accepted(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now)
    decoded = verifier.decode(encode_payload(payload))

    assert decoded is not None
    outcome = verifier.verify(decoded, snapshot, now)
    assert outcome.ok
    assert outcome.reason_code is None


def test_malformed_payload(snapshot):
    verifier = DeviceBoundQrVerifier()
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    assert verifier.decode(b"not-base64!") is None
    assert verifier.decode(base64.b64encode(b'{"v":1}')) is None
    assert verifier.decode(encode_payload({
        "v": 1, "did": "device_test", "iat": 100, "seq": 1,
        "sig": "!!!",
    })) is None

    outcome = verifier.verify(None, snapshot, now)
    assert outcome.reason_code == ReasonCode.MALFORMED
    assert not outcome.ok


def test_qr_expired(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now, iat=int(now.timestamp()) - 31)

    outcome = verifier.verify(verifier.decode(encode_payload(payload)), snapshot, now)
    assert outcome.reason_code == ReasonCode.QR_EXPIRED


def test_future_timestamp(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now, iat=int(now.timestamp()) + 6)

    outcome = verifier.verify(verifier.decode(encode_payload(payload)), snapshot, now)
    assert outcome.reason_code == ReasonCode.FUTURE


def test_unknown_device(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now, did="unknown")

    outcome = verifier.verify(verifier.decode(encode_payload(payload)), snapshot, now)
    assert outcome.reason_code == ReasonCode.UNKNOWN_DEVICE


@pytest.mark.parametrize("revocation", ["device", "employee"])
def test_revoked_device_or_employee(private_key, revocation):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    revoked_employees = frozenset({"employee_test"}) if revocation == "employee" else frozenset()
    status = "REVOKED" if revocation == "device" else "ACTIVE"
    snapshot = DeviceKeySnapshot(
        version=1,
        issued_at=now,
        signing_kid="cmp02-test",
        devices=(DeviceEntry("device_test", "employee_test", public_pem, status),),
        revoked_employees=revoked_employees,
        sig=b"snapshot-signature",
    )
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now)

    outcome = verifier.verify(verifier.decode(encode_payload(payload)), snapshot, now)
    assert outcome.reason_code == ReasonCode.DEVICE_REVOKED


def test_bad_signature_and_mutated_fields(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now)

    bad_sig = dict(payload)
    bad_sig["sig"] = base64.b64encode(b"invalid-signature").decode("ascii")
    outcome = verifier.verify(verifier.decode(encode_payload(bad_sig)), snapshot, now)
    assert outcome.reason_code == ReasonCode.BAD_SIGNATURE

    for field in ("did", "iat", "seq"):
        mutated = dict(payload)
        if field == "did":
            mutated[field] = "other_device"
        else:
            mutated[field] += 1
        outcome = verifier.verify(verifier.decode(encode_payload(mutated)), snapshot, now)
        assert outcome.reason_code in (
            ReasonCode.BAD_SIGNATURE,
            ReasonCode.UNKNOWN_DEVICE,
            ReasonCode.FUTURE,
        )


def test_unsupported_version_is_malformed(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now, v=99)

    assert verifier.decode(encode_payload(payload)) is None
    raw_payload = QrPayload(v=99, did="device_test", iat=int(now.timestamp()), seq=1, sig=b"x")
    assert verifier.verify(raw_payload, snapshot, now).reason_code == ReasonCode.MALFORMED


def test_selector_and_portal_stub():
    device_verifier = DeviceBoundQrVerifier()
    selector = QrVerifierSelector()
    qr_v1 = base64.b64encode(b'{"v":1}')
    qr_v2 = base64.b64encode(b'{"v":2}')
    qr_unknown = base64.b64encode(b'{"v":99}')

    assert isinstance(selector.select(qr_v1), DeviceBoundQrVerifier)
    assert isinstance(selector.select(qr_v2), PortalTokenVerifier)
    assert isinstance(selector.select(qr_unknown), PortalTokenVerifier)
    assert isinstance(selector.select_for_version(1), DeviceBoundQrVerifier)
    stub = PortalTokenVerifier()
    assert stub.decode(b"anything") is None
    assert not stub.verify(None, None, None).ok


def test_custom_verifier_mapping_uses_open_closed_strategy(private_key):
    custom = DeviceBoundQrVerifier()
    selector = QrVerifierSelector({7: custom})
    assert selector.select_for_version(7) is custom


def test_verifier_never_returns_none_and_outcome_is_immutable(private_key, snapshot):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    verifier = DeviceBoundQrVerifier()
    payload = signed_payload(private_key, now)
    outcome = verifier.verify(verifier.decode(encode_payload(payload)), snapshot, now)

    assert outcome is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.ok = False

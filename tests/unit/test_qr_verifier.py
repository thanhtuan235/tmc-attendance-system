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
from edge.cmp01_kiosk_control.ports.dto import (
    DeviceEntry,
    DeviceKeySnapshot,
    ReasonCode,
)


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
DID = "device_test"


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.fixture
def private_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def public_key_pem(private_key):
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


@pytest.fixture
def snapshot(public_key_pem):
    return DeviceKeySnapshot(
        version=1,
        issued_at=NOW,
        signing_kid="cmp02-test",
        devices=(DeviceEntry(
            did=DID,
            employee_ref="employee_test",
            public_key_pem=public_key_pem,
        ),),
        revoked_employees=frozenset(),
        sig=b"snapshot-signature",
    )


@pytest.fixture
def verifier():
    return DeviceBoundQrVerifier(clock=FakeClock(NOW))


def encode_payload(payload: dict) -> bytes:
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )


def sign_payload(private_key, iat: int, *, did: str = DID, seq: int = 1) -> dict:
    canonical = f"1|{did}|{iat}|{seq}".encode("utf-8")
    signature = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    return {
        "v": 1,
        "did": did,
        "iat": iat,
        "seq": seq,
        "sig": base64.b64encode(signature).decode("ascii"),
    }


@pytest.mark.parametrize("raw", [
    b"not-valid-base64!",
    base64.b64encode(b"{invalid-json"),
])
def test_malformed_json_or_base64(verifier, snapshot, raw):
    assert verifier.decode(raw) is None
    assert verifier.verify(None, snapshot, NOW).reason_code == ReasonCode.MALFORMED


@pytest.mark.parametrize("missing_field", ["v", "did", "iat", "seq", "sig"])
def test_malformed_missing_required_field(verifier, snapshot, missing_field):
    payload = sign_payload(
        ec.generate_private_key(ec.SECP256R1()),
        int(NOW.timestamp()),
    )
    del payload[missing_field]
    assert verifier.decode(encode_payload(payload)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("v", "1"),
        ("did", 123),
        ("iat", "1700000000"),
        ("seq", True),
        ("sig", 123),
    ],
)
def test_malformed_wrong_field_type(verifier, snapshot, field, value):
    payload = sign_payload(
        ec.generate_private_key(ec.SECP256R1()),
        int(NOW.timestamp()),
    )
    payload[field] = value
    assert verifier.decode(encode_payload(payload)) is None


def test_qr_expired(verifier, private_key, snapshot):
    iat = int(NOW.timestamp()) - 31
    decoded = verifier.decode(encode_payload(sign_payload(private_key, iat)))
    outcome = verifier.verify(decoded, snapshot, NOW)
    assert outcome.reason_code == ReasonCode.QR_EXPIRED


def test_future_timestamp(verifier, private_key, snapshot):
    iat = int(NOW.timestamp()) + 6
    decoded = verifier.decode(encode_payload(sign_payload(private_key, iat)))
    outcome = verifier.verify(decoded, snapshot, NOW)
    assert outcome.reason_code == ReasonCode.FUTURE


def test_unknown_device(verifier, private_key, snapshot):
    decoded = verifier.decode(encode_payload(sign_payload(
        private_key,
        int(NOW.timestamp()),
        did="unknown_device",
    )))
    outcome = verifier.verify(decoded, snapshot, NOW)
    assert outcome.reason_code == ReasonCode.UNKNOWN_DEVICE


def test_device_revoked(verifier, private_key, public_key_pem):
    revoked_snapshot = DeviceKeySnapshot(
        version=1,
        issued_at=NOW,
        signing_kid="cmp02-test",
        devices=(DeviceEntry(DID, "employee_test", public_key_pem, "REVOKED"),),
        revoked_employees=frozenset(),
        sig=b"snapshot-signature",
    )
    decoded = verifier.decode(encode_payload(sign_payload(
        private_key,
        int(NOW.timestamp()),
    )))
    outcome = verifier.verify(decoded, revoked_snapshot, NOW)
    assert outcome.reason_code == ReasonCode.DEVICE_REVOKED


def test_employee_revoked(verifier, private_key, public_key_pem):
    revoked_snapshot = DeviceKeySnapshot(
        version=1,
        issued_at=NOW,
        signing_kid="cmp02-test",
        devices=(DeviceEntry(DID, "employee_test", public_key_pem, "ACTIVE"),),
        revoked_employees=frozenset({"employee_test"}),
        sig=b"snapshot-signature",
    )
    decoded = verifier.decode(encode_payload(sign_payload(
        private_key,
        int(NOW.timestamp()),
    )))
    outcome = verifier.verify(decoded, revoked_snapshot, NOW)
    assert outcome.reason_code == ReasonCode.DEVICE_REVOKED


def test_bad_signature_after_exactly_one_signature_byte_changed(
    verifier,
    private_key,
    snapshot,
):
    payload = sign_payload(private_key, int(NOW.timestamp()))
    signature = bytearray(base64.b64decode(payload["sig"], validate=True))
    assert len(signature) > 0
    signature[-1] ^= 0x01
    payload["sig"] = base64.b64encode(bytes(signature)).decode("ascii")

    decoded = verifier.decode(encode_payload(payload))
    outcome = verifier.verify(decoded, snapshot, NOW)
    assert outcome.reason_code == ReasonCode.BAD_SIGNATURE


def test_valid_qr_is_accepted(verifier, private_key, snapshot):
    payload = sign_payload(private_key, int(NOW.timestamp()), seq=42)
    decoded = verifier.decode(encode_payload(payload))

    assert decoded is not None
    outcome = verifier.verify(decoded, snapshot, NOW)
    assert outcome.ok
    assert outcome.reason_code is None


def test_mutated_signed_fields_are_rejected(
    verifier,
    private_key,
    snapshot,
):
    original = sign_payload(private_key, int(NOW.timestamp()), seq=42)
    for field, replacement in (
        ("did", "changed_device"),
        ("iat", int(NOW.timestamp()) + 1),
        ("seq", 43),
    ):
        mutated = dict(original)
        mutated[field] = replacement
        decoded = verifier.decode(encode_payload(mutated))
        outcome = verifier.verify(decoded, snapshot, NOW)
        assert outcome.reason_code in (
            ReasonCode.BAD_SIGNATURE,
            ReasonCode.UNKNOWN_DEVICE,
            ReasonCode.FUTURE,
        )


def test_selector_and_portal_stub():
    selector = QrVerifierSelector()
    assert isinstance(selector.select(base64.b64encode(b'{"v":1}')), DeviceBoundQrVerifier)
    assert isinstance(selector.select(base64.b64encode(b'{"v":2}')), PortalTokenVerifier)
    assert isinstance(selector.select(base64.b64encode(b'{"v":99}')), PortalTokenVerifier)


def test_verify_outcome_is_immutable(verifier, private_key, snapshot):
    decoded = verifier.decode(encode_payload(
        sign_payload(private_key, int(NOW.timestamp()))
    ))
    outcome = verifier.verify(decoded, snapshot, NOW)

    assert outcome is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.ok = False

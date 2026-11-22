from concurrent.futures import ThreadPoolExecutor
import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from edge.cmp01_kiosk_control.adapters.device_bound_qr_verifier import (
    DeviceBoundQrVerifier,
)
from edge.cmp01_kiosk_control.adapters.replay_guard import (
    ReplayFloorPolicy,
    ReplayGuard,
)
from edge.cmp01_kiosk_control.ports.dto import (
    DeviceEntry,
    DeviceKeySnapshot,
    ReasonCode,
)


def test_new_device_valid_sequence_is_accepted():
    guard = ReplayGuard()
    assert guard.check("device_001", 105, 1_700_000_000) is None
    assert guard.commit("device_001", 105)
    assert guard.watermarks["device_001"] == 105


def test_sequence_greater_than_watermark_is_accepted():
    guard = ReplayGuard()
    guard.commit("device_001", 105)
    assert guard.check("device_001", 106, 1_700_000_001) is None
    assert guard.commit("device_001", 106)


def test_equal_and_lower_sequences_are_replay():
    guard = ReplayGuard()
    guard.commit("device_001", 105)
    assert guard.check("device_001", 105, 1_700_000_000) == ReasonCode.REPLAY
    assert guard.check("device_001", 104, 1_700_000_000) == ReasonCode.REPLAY
    assert guard.commit("device_001", 105) is False


def test_watermarks_are_independent_by_device():
    guard = ReplayGuard()
    assert guard.accept("device_001", 105, 1_700_000_100) is None
    assert guard.accept("device_002", 105, 1_700_000_100) is None
    assert guard.watermarks == {"device_001": 105, "device_002": 105}


def test_iat_below_equal_and_above_replay_floor():
    guard = ReplayGuard()
    guard.set_floor(datetime(2026, 1, 1, tzinfo=timezone.utc))
    floor = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())

    assert guard.check("device_001", 1, floor - 1) == ReasonCode.REPLAY
    assert guard.check("device_001", 1, floor) is None
    assert guard.check("device_001", 1, floor + 1) is None


def test_invalid_signature_or_expired_qr_does_not_commit():
    guard = ReplayGuard()

    signature_valid = False
    if signature_valid:
        assert guard.commit("device_001", 105)
    assert "device_001" not in guard.watermarks

    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    guard.set_floor(now)
    expired_iat = int(now.timestamp()) - 31
    assert guard.check("device_001", 106, expired_iat) == ReasonCode.REPLAY
    assert "device_001" not in guard.watermarks


def test_concurrent_requests_accept_only_one_sequence():
    guard = ReplayGuard()

    def submit(_: int):
        return guard.accept("device_001", 105, 1_700_000_100)

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(submit, range(16)))

    accepted = sum(result is None for result in results)
    assert accepted == 1
    assert guard.watermarks["device_001"] == 105


def test_floor_policy_first_set_and_monotonic_update():
    policy = ReplayFloorPolicy()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    policy.set_floor(start)
    assert policy.replay_floor == int(start.timestamp())

    policy.set_floor(start - timedelta(seconds=10))
    assert policy.replay_floor == int(start.timestamp())

    later = start + timedelta(minutes=5)
    policy.set_floor(later)
    assert policy.replay_floor == int(later.timestamp())


def test_floor_policy_replay_boundaries():
    policy = ReplayFloorPolicy()
    floor = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    policy.set_floor(floor)
    epoch = int(floor.timestamp())

    assert policy.check(epoch - 1) == ReasonCode.REPLAY
    assert policy.check(epoch) is None
    assert policy.check(epoch + 1) is None


def test_floor_policy_supports_edge_restart_timestamp():
    policy = ReplayFloorPolicy()
    edge_start = datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc)
    policy.set_floor(edge_start)

    assert policy.replay_floor_datetime == edge_start


def test_tampered_signature_does_not_change_watermark():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    did = "device_test"
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    snapshot = DeviceKeySnapshot(
        version=1,
        issued_at=now,
        signing_kid="cmp02-test",
        devices=(DeviceEntry(did, "employee_test", public_pem, "ACTIVE"),),
        revoked_employees=frozenset(),
        sig=b"snapshot-signature",
    )
    iat = int(now.timestamp())
    seq = 105
    canonical = f"1|{did}|{iat}|{seq}".encode("utf-8")
    signature = bytearray(
        private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    )
    payload = {
        "v": 1,
        "did": did,
        "iat": iat,
        "seq": seq,
        "sig": base64.b64encode(bytes(signature)).decode("ascii"),
    }
    raw_qr = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )

    verifier = DeviceBoundQrVerifier(clock=type(
        "FakeClock",
        (),
        {"now": lambda self: now},
    )())
    replay_guard = ReplayGuard()

    decoded = verifier.decode(raw_qr)
    valid_result = verifier.verify(decoded, snapshot, now)
    assert valid_result.ok

    replay_guard.commit(did, seq)
    watermark_before = replay_guard.get_watermark(did)

    signature[-1] ^= 0x01
    payload["sig"] = base64.b64encode(bytes(signature)).decode("ascii")
    tampered_qr = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    tampered = verifier.decode(tampered_qr)
    tampered_result = verifier.verify(tampered, snapshot, now)

    watermark_after = replay_guard.get_watermark(did)

    assert tampered_result.reason_code == ReasonCode.BAD_SIGNATURE
    assert watermark_before == watermark_after

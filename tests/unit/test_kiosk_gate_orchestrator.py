"""Unit test T1-03: thứ tự xử lý và các nhánh của KioskGateOrchestrator."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edge.cmp01_kiosk_control import dev_stubs as ds
from edge.cmp01_kiosk_control.config import EdgeConfig
from edge.cmp01_kiosk_control.entry.scan_api import create_app
from edge.cmp01_kiosk_control.orchestration import kiosk_gate_orchestrator as mod
from edge.cmp01_kiosk_control.orchestration.kiosk_gate_orchestrator import (
    KioskGateOrchestrator, compute_event_id,
)
from edge.cmp01_kiosk_control.ports import (
    Direction, FreshnessDecision, GateAck, ReasonCode, ScanRequest, ScanStatus,
    SnapshotTier, TxStatus, VerifyOutcome,
)

IAT = int(ds.FakeClock().now().timestamp())


class Rig:
    def __init__(self, *, verify=None, freshness=None, ack=GateAck.OPENED):
        self.calls: list[str] = []
        self.outbox = ds.InMemoryOutbox(self.calls)
        self.replay = ds.InMemoryReplayGuard(self.calls)
        self.turnstile = ds.StubTurnstile(self.calls, ack)
        self.orch = KioskGateOrchestrator(
            verifiers=[ds.StubVerifier(self.calls, verify)],
            snapshot_store=ds.StubSnapshotStore(),
            freshness_policy=ds.StubFreshnessPolicy(self.calls, freshness),
            replay_guard=self.replay,
            outbox=self.outbox,
            turnstile=self.turnstile,
            clock=ds.FakeClock(),
            config=EdgeConfig(),
        )

    def scan(self, seq=1, direction=Direction.IN, station="ST-01", raw=None, sig=b"sig"):
        raw = raw if raw is not None else ds.encode_qr(1, "dev-001", IAT, seq, sig)
        return self.orch.submit_scan(ScanRequest(station, "G-01", direction, raw))


HAPPY_ORDER = [
    "decode", "outbox.find", "verify", "freshness:IN", "replay.check",
    "outbox.insert_received", "replay.commit", "outbox.mark_command_sent",
    "turnstile.open_gate", "outbox.mark_opened",
]


def test_1_happy_path_order_and_state():
    rig = Rig()
    res = rig.scan()
    assert rig.calls == HAPPY_ORDER
    assert res.status == ScanStatus.ACCEPTED and res.tx_status == TxStatus.OPENED
    assert res.event_id == compute_event_id("ST-01", "dev-001", 1)
    row = rig.outbox.rows[res.event_id]
    assert row.status == TxStatus.OPENED and row.command_sent_at is not None
    assert rig.turnstile.commands == [res.event_id]  # command_id = eventId
    assert rig.replay.watermarks["dev-001"] == 1


def test_2_resend_same_station_returns_previous_result():
    rig = Rig()
    first = rig.scan()
    rig.calls.clear()
    again = rig.scan()
    assert rig.calls == ["decode", "outbox.find"]
    assert again.replayed_result and again.tx_status == first.tx_status
    assert len(rig.turnstile.commands) == 1  # không mở cổng lần hai


def test_2b_same_event_id_different_sig_is_not_trusted():
    rig = Rig()
    rig.scan(sig=b"sig")
    res = rig.scan(sig=b"forged")
    assert res.status == ScanStatus.REJECTED and res.reason_code == ReasonCode.REPLAY


def test_malformed_qr():
    rig = Rig()
    res = rig.scan(raw=b"not-a-qr")
    assert res.reason_code == ReasonCode.MALFORMED
    assert "outbox.find" not in rig.calls and not rig.outbox.rows


@pytest.mark.parametrize("code", [
    ReasonCode.QR_EXPIRED, ReasonCode.FUTURE, ReasonCode.UNKNOWN_DEVICE,
    ReasonCode.DEVICE_REVOKED, ReasonCode.BAD_SIGNATURE,
])
def test_3_verifier_rejects_stop_before_replay_and_gate(code):
    rig = Rig(verify=VerifyOutcome(ok=False, reason_code=code))
    res = rig.scan()
    assert res.status == ScanStatus.REJECTED and res.reason_code == code
    assert rig.calls[-1] == "verify"
    assert not rig.outbox.rows and not rig.turnstile.commands


def test_4_replay_other_station_same_seq():
    rig = Rig()
    rig.scan(station="ST-01")
    res = rig.scan(station="ST-02")  # eventId khác, seq đã dùng
    assert res.reason_code == ReasonCode.REPLAY
    assert len(rig.outbox.rows) == 1 and len(rig.turnstile.commands) == 1


EXPIRED_IN = FreshnessDecision(tier=SnapshotTier.EXPIRED, allow=False,
                               reason_code=ReasonCode.STALE_SNAPSHOT)
EXPIRED_OUT = FreshnessDecision(tier=SnapshotTier.EXPIRED, allow=True, needs_review=True)


def test_5_expired_snapshot_blocks_in():
    rig = Rig(freshness={Direction.IN: EXPIRED_IN, Direction.OUT: EXPIRED_OUT})
    res = rig.scan(direction=Direction.IN)
    assert res.reason_code == ReasonCode.STALE_SNAPSHOT
    assert "replay.check" not in rig.calls and not rig.turnstile.commands


def test_6_expired_snapshot_allows_out_with_review_flag():
    rig = Rig(freshness={Direction.IN: EXPIRED_IN, Direction.OUT: EXPIRED_OUT})
    res = rig.scan(direction=Direction.OUT)
    assert res.status == ScanStatus.ACCEPTED and res.needs_review
    row = rig.outbox.rows[res.event_id]
    assert row.snapshot_tier == SnapshotTier.EXPIRED and row.needs_review


def test_stale_snapshot_flagged():
    stale = FreshnessDecision(tier=SnapshotTier.STALE, allow=True, stale_snapshot=True)
    rig = Rig(freshness={Direction.IN: stale})
    assert rig.scan().stale_snapshot


@pytest.mark.parametrize("ack,status,mark", [
    (GateAck.UNCONFIRMED, TxStatus.UNCONFIRMED, "outbox.mark_unconfirmed"),
    (GateAck.FAILED, TxStatus.FAILED, "outbox.mark_failed"),
])
def test_7_gate_not_opened_no_blind_retry(ack, status, mark):
    rig = Rig(ack=ack)
    res = rig.scan()
    assert res.tx_status == status and rig.calls[-1] == mark
    assert rig.calls.count("turnstile.open_gate") == 1


def test_never_raises_to_kiosk():
    rig = Rig()
    rig.turnstile.open_gate = lambda *a, **k: 1 / 0
    res = rig.scan()
    assert res.status == ScanStatus.REJECTED


def test_orchestrator_depends_only_on_ports():
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "adapters" not in node.module and "dev_stubs" not in node.module


def test_scan_api_roundtrip():
    rig = Rig()
    client = TestClient(create_app(rig.orch))
    assert client.get("/health").json() == {"status": "ok"}
    body = {"station_id": "ST-01", "gate_id": "G-01", "direction": "IN",
            "qr": ds.encode_qr(1, "dev-001", IAT, 5).decode()}
    r = client.post("/scan", json=body)
    assert r.status_code == 200
    assert r.json()["status"] == "ACCEPTED" and r.json()["tx_status"] == "OPENED"
    r2 = client.post("/scan", json={**body, "qr": "garbage"})
    assert r2.json()["reason_code"] == "MALFORMED"

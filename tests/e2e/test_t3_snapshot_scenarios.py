from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cloud.cmp03_attendance_mini.main import create_app as create_cloud_app
from edge.cmp01_kiosk_control.adapters.snapshot_store import SnapshotStore
from edge.cmp01_kiosk_control.config import EdgeConfig
from edge.cmp01_kiosk_control.ports import Direction, IClock, ReasonCode, SnapshotTier, VerifyOutcome
from edge.cmp01_kiosk_control.snapshot_freshness_policy import SnapshotFreshnessPolicy
from packages.contracts import AttendanceEvent
from tools import fake_cmp02, fake_mobile


@pytest.fixture
def edge(tmp_path, monkeypatch):
    scan_api = pytest.importorskip("edge.cmp01_kiosk_control.entry.scan_api")
    orchestrator = pytest.importorskip("edge.cmp01_kiosk_control.orchestration.kiosk_gate_orchestrator")
    verifier_adapter = pytest.importorskip("edge.cmp01_kiosk_control.adapters.device_bound_qr_verifier")
    replay = pytest.importorskip("edge.cmp01_kiosk_control.adapters.replay_guard")
    sqlite_outbox = pytest.importorskip("edge.cmp01_kiosk_control.adapters.sqlite_outbox")
    fake_turnstile = pytest.importorskip("edge.cmp01_kiosk_control.adapters.fake_turnstile_adapter")
    monkeypatch.setattr(fake_mobile, "DEVICES_DIR", tmp_path / "devices")

    class FixedClock(IClock):
        def now(self):
            return datetime(2026, 11, 20, 12, tzinfo=timezone.utc)

    clock = FixedClock()
    config = EdgeConfig()
    policy = SnapshotFreshnessPolicy(config)
    verifier = verifier_adapter.DeviceBoundQrVerifier(
        clock, window_past_sec=config.qr_window_past_sec, window_future_sec=config.qr_window_future_sec,
    )
    turnstile = fake_turnstile.FakeTurnstileAdapter(mode="OK", clock=clock)
    root_public = tmp_path / "root_pub.pem"
    snapshot_file = tmp_path / "snapshot.json"

    def publish(version, age):
        assert fake_cmp02.main([
            "--device", "device_001", "employee_001", "--version", str(version),
            "--issued-at", (clock.now() - age).isoformat(),
            "--signing-key", str(tmp_path / "root.pem"),
            "--root-public-key", str(root_public), "--output", str(snapshot_file),
        ]) == 0
        return snapshot_file.read_bytes()

    first_snapshot = publish(1, timedelta())
    store = SnapshotStore(tmp_path / "edge_cache.db", root_public)
    assert store.load(first_snapshot)
    with closing(sqlite_outbox.SqliteWalOutbox(tmp_path / "edge_tx.db")) as outbox:
        gate = orchestrator.KioskGateOrchestrator(
            verifiers=[verifier], snapshot_store=store, freshness_policy=policy,
            replay_guard=replay.ReplayGuard(), outbox=outbox, turnstile=turnstile,
            clock=clock, config=config,
        )
        with TestClient(scan_api.create_app(gate)) as client:
            def scan(seq, direction="IN"):
                qr, _ = fake_mobile.generate_qr("device_001", iat=int(clock.now().timestamp()), seq=seq)
                response = client.post("/scan", json={
                    "station_id": "ST-01", "gate_id": "G-01", "direction": direction, "qr": qr,
                })
                assert response.status_code == 200
                return response.json()

            yield clock, publish, scan, store, policy, turnstile, outbox, verifier


def test_s3_signed_snapshot_age_controls_scan_and_flags_cloud_event(edge, tmp_path):
    clock, publish, scan, store, policy, turnstile, outbox, _ = edge
    fresh = scan(1)
    assert fresh["tx_status"] == "OPENED"
    assert not fresh["stale_snapshot"]

    assert store.load(publish(2, timedelta(minutes=16)))
    assert policy.evaluate(store.current(), Direction.IN, clock.now()).warn
    stale = scan(2)
    assert stale["tx_status"] == "OPENED"
    assert stale["stale_snapshot"]

    assert store.load(publish(3, timedelta(hours=4, seconds=1)))
    denied = scan(3)
    assert denied["status"] == "REJECTED"
    assert denied["reason_code"] == "STALE_SNAPSHOT"
    exit_scan = scan(4, "OUT")
    assert exit_scan["tx_status"] == "OPENED"
    assert exit_scan["needs_review"]
    assert turnstile.calls == [fresh["event_id"], stale["event_id"], exit_scan["event_id"]]
    assert outbox.find(denied["event_id"]) is None

    record = outbox.find(exit_scan["event_id"])
    assert record.snapshot_tier is SnapshotTier.EXPIRED
    event = AttendanceEvent(
        event_id=record.event_id, station_id=record.station_id, station_seq=record.station_seq,
        gate_id=record.gate_id, did=record.did, seq=record.seq, direction=record.direction.value,
        status=record.status.value, credential_type=record.credential_type.value,
        snapshot_tier=record.snapshot_tier.value, needs_review=record.needs_review,
        occurred_at=record.created_at.isoformat(), ack_at=record.ack_at.isoformat(),
    )
    with TestClient(create_cloud_app(tmp_path / "cmp03_events.db")) as cloud:
        batch = {"events": [event.to_dict()]}
        first = cloud.post("/events", json=batch)
        retry = cloud.post("/events", json=batch)
        assert first.status_code == retry.status_code == 200
        assert first.json() == {"accepted": 1, "duplicates": 0}
        assert retry.json() == {"accepted": 0, "duplicates": 1}
        assert cloud.get("/events/count").json() == {"count": 1}


def test_s6_clock_skew_outcome_is_audited_and_does_not_open_gate(edge, monkeypatch, caplog):
    _, _, scan, _, _, turnstile, outbox, verifier = edge
    monkeypatch.setattr(verifier, "verify", lambda *_: VerifyOutcome(False, ReasonCode.CLOCK_SKEW))
    with caplog.at_level("INFO", logger="cmp01.audit"):
        response = scan(1)
    assert response["status"] == "REJECTED"
    assert response["reason_code"] == "CLOCK_SKEW"
    assert "reason=CLOCK_SKEW" in caplog.text
    assert turnstile.calls == []
    assert outbox.find(response["event_id"]) is None

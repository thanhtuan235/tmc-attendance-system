"""T4-04 – SyncAgent + HttpCloudClient (ADR vấn đề 1: không mất, không trùng bản ghi)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.cmp03_attendance_mini.main import create_app as create_cloud_app
from edge.cmp01_kiosk_control.adapters.http_cloud_client import HttpCloudClient
from edge.cmp01_kiosk_control.adapters.sqlite_outbox import SqliteWalOutbox
from edge.cmp01_kiosk_control.orchestration.sync_agent import SyncAgent, to_event
from edge.cmp01_kiosk_control.ports import (
    CredentialType, Direction, ICloudEventClient, OutboxRecord, SnapshotTier, TxStatus,
)

T0 = datetime(2026, 11, 20, 0, 15, 3, tzinfo=timezone.utc)


def add_final(outbox, n, start=1, status="opened"):
    for i in range(start, start + n):
        eid = f"e{i}"
        outbox.insert_received(OutboxRecord(
            event_id=eid, qr_hash=f"h{i}", station_id="ST-01", gate_id="G-01", did="dev-001",
            seq=i, direction=Direction.IN, credential_type=CredentialType.DEVICE_BOUND,
            status=TxStatus.RECEIVED, snapshot_tier=SnapshotTier.FRESH, created_at=T0,
        ))
        getattr(outbox, f"mark_{status}")(eid, T0 + timedelta(seconds=1))


class FlakyCloud(ICloudEventClient):
    """Cloud giả: lỗi `fail_times` lần đầu rồi nhận; lưu theo event_id như CMP-03."""

    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.calls = 0
        self.stored: dict[str, dict] = {}

    def send_batch(self, events):
        self.calls += 1
        if self.calls <= self.fail_times:
            return False
        for e in events:
            self.stored.setdefault(e.event_id, e.to_dict())
        return True


@pytest.fixture
def outbox(tmp_path):
    ob = SqliteWalOutbox(tmp_path / "edge_tx.db")
    yield ob
    ob.close()


def test_pushes_final_records_and_marks_synced(outbox):
    add_final(outbox, 3)
    cloud = FlakyCloud()
    agent = SyncAgent(outbox, cloud)
    assert agent.run_once() == 3
    assert set(cloud.stored) == {"e1", "e2", "e3"}
    assert agent.pending_count() == 0


def test_received_records_are_not_sent(outbox):
    add_final(outbox, 1)
    outbox.insert_received(OutboxRecord(
        event_id="pending", qr_hash="hp", station_id="ST-01", gate_id="G-01", did="dev-001",
        seq=99, direction=Direction.IN, credential_type=CredentialType.DEVICE_BOUND,
        status=TxStatus.RECEIVED, snapshot_tier=SnapshotTier.FRESH, created_at=T0,
    ))
    cloud = FlakyCloud()
    SyncAgent(outbox, cloud).run_once()
    assert set(cloud.stored) == {"e1"}


def test_cloud_down_keeps_records_then_recovers_without_loss(outbox):
    add_final(outbox, 4)
    cloud = FlakyCloud(fail_times=2)
    agent = SyncAgent(outbox, cloud, interval_sec=1, max_backoff_sec=8)

    for expected_delay in (2, 4):
        with pytest.raises(ConnectionError):
            agent.run_once()
        assert agent.pending_count() == 4
        assert agent.next_delay() == expected_delay

    assert agent.run_once() == 4
    assert agent.consecutive_failures == 0
    assert agent.next_delay() == 1
    assert len(cloud.stored) == 4


def test_backoff_is_capped(outbox):
    agent = SyncAgent(outbox, FlakyCloud(), interval_sec=3, max_backoff_sec=30)
    agent._failures = 10
    assert agent.next_delay() == 30


def test_batches_respect_batch_size(outbox):
    add_final(outbox, 5)
    cloud = FlakyCloud()
    assert SyncAgent(outbox, cloud, batch_size=2).run_once() == 5
    assert cloud.calls == 3


def test_resend_after_lost_ack_creates_no_duplicate_in_cmp03(outbox, tmp_path):
    """Gửi lại cùng lô (mất ACK) → CMP-03 vẫn chỉ có 1 bản ghi mỗi event_id."""
    add_final(outbox, 2, status="unconfirmed")
    events = [to_event(r) for r in outbox.list_unsynced()]
    with TestClient(create_cloud_app(tmp_path / "cmp03.db")) as cloud_http:
        client = HttpCloudClient("http://cmp03/events", client=cloud_http)
        assert client.send_batch(events) is True
        assert client.send_batch(events) is True
        assert cloud_http.get("/events/count").json() == {"count": 2}


def test_http_client_returns_false_when_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client = HttpCloudClient("http://cmp03/events", client=httpx.Client(transport=httpx.MockTransport(refuse)))
    assert client.send_batch([to_event_dummy()]) is False


def test_http_client_returns_false_on_5xx_and_400():
    for code in (503, 400):
        transport = httpx.MockTransport(lambda req, c=code: httpx.Response(c, json={"error": "x"}))
        client = HttpCloudClient("http://cmp03/events", client=httpx.Client(transport=transport))
        assert client.send_batch([to_event_dummy()]) is False


def to_event_dummy():
    return to_event(OutboxRecord(
        event_id="x", qr_hash="h", station_id="ST-01", station_seq=1, gate_id="G-01", did="d",
        seq=1, direction=Direction.OUT, credential_type=CredentialType.DEVICE_BOUND,
        status=TxStatus.OPENED, snapshot_tier=SnapshotTier.STALE, created_at=T0,
        needs_review=True, ack_at=T0,
    ))


def test_to_event_maps_fields():
    e = to_event_dummy()
    assert (e.direction, e.status, e.snapshot_tier, e.needs_review) == ("OUT", "OPENED", "STALE", True)
    assert e.occurred_at == T0.isoformat() and e.ack_at == T0.isoformat()

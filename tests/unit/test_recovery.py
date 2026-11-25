"""T4-03 – StartupRecovery (ADR-010: dấu mốc lệnh mở, No Blind Retry, sàn replay)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edge.cmp01_kiosk_control.adapters.replay_guard import ReplayGuard
from edge.cmp01_kiosk_control.adapters.sqlite_outbox import SqliteWalOutbox
from edge.cmp01_kiosk_control.orchestration.recovery import StartupRecovery
from edge.cmp01_kiosk_control.ports import (
    CredentialType, Direction, IClock, OutboxRecord, ReasonCode, SnapshotTier, TxStatus,
)

T0 = datetime(2026, 11, 20, 0, 15, 3, tzinfo=timezone.utc)
BOOT = T0 + timedelta(minutes=10)


class Clock(IClock):
    def now(self):
        return BOOT


def received(outbox, eid, seq):
    outbox.insert_received(OutboxRecord(
        event_id=eid, qr_hash=f"h-{eid}", station_id="ST-01", gate_id="G-01", did="dev-001",
        seq=seq, direction=Direction.IN, credential_type=CredentialType.DEVICE_BOUND,
        status=TxStatus.RECEIVED, snapshot_tier=SnapshotTier.FRESH, created_at=T0,
    ))


@pytest.fixture
def db(tmp_path):
    return tmp_path / "edge_tx.db"


def test_recovery_after_crash(db):
    # Trước khi sập: 1 bản ghi đã gửi lệnh mở nhưng chưa ACK, 1 chưa kịp gửi lệnh, 1 đã OPENED.
    ob = SqliteWalOutbox(db)
    received(ob, "sent", 1)
    ob.mark_command_sent("sent", T0)
    received(ob, "not-sent", 2)
    received(ob, "done", 3)
    ob.mark_command_sent("done", T0)
    ob.mark_opened("done", T0)
    ob.close()

    # Khởi động lại: mở lại file, chạy Recovery.
    ob = SqliteWalOutbox(db)
    guard = ReplayGuard()
    report = StartupRecovery(ob, guard, Clock()).run()

    assert (report.unconfirmed, report.failed) == (1, 1)
    assert ob.find("sent").status == TxStatus.UNCONFIRMED
    assert ob.find("not-sent").status == TxStatus.FAILED
    assert ob.find("done").status == TxStatus.OPENED          # kết quả cuối giữ nguyên
    assert ob.list_pending_recovery() == []
    assert {r.event_id for r in ob.list_unsynced()} == {"sent", "not-sent", "done"}
    ob.close()


def test_recovery_sets_replay_floor_at_boot(db):
    ob = SqliteWalOutbox(db)
    guard = ReplayGuard()
    StartupRecovery(ob, guard, Clock()).run()
    before_boot = int((BOOT - timedelta(seconds=1)).timestamp())
    after_boot = int((BOOT + timedelta(seconds=1)).timestamp())
    assert guard.check("dev-001", 1, before_boot) == ReasonCode.REPLAY
    assert guard.check("dev-001", 1, after_boot) is None
    ob.close()


def test_recovery_is_idempotent(db):
    ob = SqliteWalOutbox(db)
    received(ob, "sent", 1)
    ob.mark_command_sent("sent", T0)
    rec = StartupRecovery(ob, ReplayGuard(), Clock())
    assert rec.run().unconfirmed == 1
    assert rec.run().unconfirmed == 0
    ob.close()

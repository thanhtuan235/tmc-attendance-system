"""T4-01 – SqliteWalOutbox (IOutboxRepository, ADR-010, Δ-C1.6)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from edge.cmp01_kiosk_control.adapters.sqlite_outbox import SqliteWalOutbox
from edge.cmp01_kiosk_control.ports import (
    CredentialType, Direction, IOutboxRepository, OutboxRecord, SnapshotTier, TxStatus,
)

T0 = datetime(2026, 11, 20, 0, 15, 3, tzinfo=timezone.utc)


def make_record(event_id="e1", station_id="ST-01", seq=1, **kw) -> OutboxRecord:
    base = dict(
        event_id=event_id, qr_hash=f"h-{event_id}", station_id=station_id, gate_id="G-01",
        did="dev-001", seq=seq, direction=Direction.IN,
        credential_type=CredentialType.DEVICE_BOUND, status=TxStatus.RECEIVED,
        snapshot_tier=SnapshotTier.FRESH, created_at=T0,
    )
    base.update(kw)
    return OutboxRecord(**base)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "edge_tx.db"


@pytest.fixture
def outbox(db_path):
    ob = SqliteWalOutbox(db_path)
    yield ob
    ob.close()


def test_implements_port(outbox):
    assert isinstance(outbox, IOutboxRepository)


def test_uses_wal_and_synchronous_full(outbox, db_path):
    con = sqlite3.connect(db_path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        con.close()
    assert outbox.pragma("synchronous") == 2  # FULL
    assert outbox.pragma("journal_mode").lower() == "wal"


def test_insert_received_assigns_station_seq_and_roundtrips(outbox):
    saved = outbox.insert_received(make_record("e1"))
    assert saved.station_seq == 1
    found = outbox.find("e1")
    assert found == saved
    assert found.status is TxStatus.RECEIVED and found.direction is Direction.IN
    assert found.command_sent_at is None and found.ack_at is None and found.synced is False


def test_find_unknown_returns_none(outbox):
    assert outbox.find("nope") is None


def test_station_seq_increases_per_station(outbox):
    a1 = outbox.insert_received(make_record("a1", station_id="ST-01"))
    a2 = outbox.insert_received(make_record("a2", station_id="ST-01"))
    b1 = outbox.insert_received(make_record("b1", station_id="ST-02"))
    assert (a1.station_seq, a2.station_seq, b1.station_seq) == (1, 2, 1)


def test_insert_is_idempotent_by_event_id(outbox):
    first = outbox.insert_received(make_record("e1"))
    again = outbox.insert_received(make_record("e1", qr_hash="khac", seq=99))
    assert again == first                       # trả lại bản ghi cũ, không ghi đè
    nxt = outbox.insert_received(make_record("e2"))
    assert nxt.station_seq == 2                 # lần chèn trùng không tiêu tốn station_seq


def test_two_event_ids_same_did_seq_are_both_stored(outbox):
    """(did, seq) không duy nhất: phát hiện dùng lại QR ở CMP-03 (ADR-014)."""
    outbox.insert_received(make_record("e1", did="d", seq=5))
    outbox.insert_received(make_record("e2", did="d", seq=5))
    assert outbox.find("e1") and outbox.find("e2")


def test_data_survives_reopen(db_path):
    ob = SqliteWalOutbox(db_path)
    ob.insert_received(make_record("e1"))
    ob.mark_command_sent("e1", T0 + timedelta(milliseconds=20))
    ob.close()

    reopened = SqliteWalOutbox(db_path)
    try:
        rec = reopened.find("e1")
        assert rec is not None and rec.station_seq == 1
        assert rec.command_sent_at == T0 + timedelta(milliseconds=20)
        # station_seq tiếp tục sau khi mở lại file
        assert reopened.insert_received(make_record("e2")).station_seq == 2
    finally:
        reopened.close()


def test_command_marker_is_durable_before_turnstile_call(db_path, outbox):
    """ADR-010: dấu mốc phải đọc được từ một kết nối khác ngay sau mark_command_sent."""
    outbox.insert_received(make_record("e1"))
    assert outbox.mark_command_sent("e1", T0) is True
    other = SqliteWalOutbox(db_path)
    try:
        assert other.find("e1").command_sent_at == T0
    finally:
        other.close()


@pytest.mark.parametrize("method,expected", [
    ("mark_opened", TxStatus.OPENED),
    ("mark_unconfirmed", TxStatus.UNCONFIRMED),
    ("mark_failed", TxStatus.FAILED),
])
def test_mark_final_status_sets_ack_at(outbox, method, expected):
    outbox.insert_received(make_record("e1"))
    at = T0 + timedelta(seconds=1)
    assert getattr(outbox, method)("e1", at) is True
    rec = outbox.find("e1")
    assert rec.status is expected and rec.ack_at == at


@pytest.mark.parametrize("method", [
    "mark_command_sent", "mark_opened", "mark_unconfirmed", "mark_failed",
])
def test_mark_unknown_event_returns_false(outbox, method):
    assert getattr(outbox, method)("khong-co", T0) is False


def test_final_status_is_not_overwritten(outbox):
    """Append-only (ADR-006): đã có kết quả cuối thì không đổi lại."""
    outbox.insert_received(make_record("e1"))
    outbox.mark_opened("e1", T0)
    assert outbox.mark_failed("e1", T0 + timedelta(seconds=5)) is True
    rec = outbox.find("e1")
    assert rec.status is TxStatus.OPENED and rec.ack_at == T0


def test_list_pending_recovery_returns_received_only(outbox):
    outbox.insert_received(make_record("r1"))                    # chưa có dấu mốc
    outbox.insert_received(make_record("r2"))
    outbox.mark_command_sent("r2", T0)                            # có dấu mốc, chưa có kết quả
    outbox.insert_received(make_record("done"))
    outbox.mark_opened("done", T0)
    ids = [r.event_id for r in outbox.list_pending_recovery()]
    assert ids == ["r1", "r2"]


def test_list_unsynced_only_final_status_in_station_seq_order(outbox):
    for eid in ("a", "b", "c", "d"):
        outbox.insert_received(make_record(eid))
    outbox.mark_failed("c", T0)
    outbox.mark_opened("a", T0)
    outbox.mark_unconfirmed("d", T0)                              # b còn RECEIVED
    assert [r.event_id for r in outbox.list_unsynced()] == ["a", "c", "d"]


def test_list_unsynced_respects_limit(outbox):
    for i in range(5):
        outbox.insert_received(make_record(f"e{i}"))
        outbox.mark_opened(f"e{i}", T0)
    assert len(outbox.list_unsynced(limit=2)) == 2


def test_mark_synced_removes_from_unsynced_and_ignores_unknown(outbox):
    for eid in ("a", "b"):
        outbox.insert_received(make_record(eid))
        outbox.mark_opened(eid, T0)
    outbox.mark_synced(["a", "khong-co"])
    assert [r.event_id for r in outbox.list_unsynced()] == ["b"]
    assert outbox.find("a").synced is True
    outbox.mark_synced([])                                        # rỗng: không lỗi


def test_optional_fields_roundtrip(outbox):
    rec = make_record("e1", needs_review=True, snapshot_tier=SnapshotTier.STALE,
                      direction=Direction.OUT)
    outbox.insert_received(rec)
    got = outbox.find("e1")
    assert got.needs_review is True and got.snapshot_tier is SnapshotTier.STALE
    assert got.direction is Direction.OUT and got.reason_code is None
    assert replace(got, station_seq=None) == rec

"""Cài đặt giả trong bộ nhớ cho mọi port (T1-03).

Dùng cho unit test và để chạy Scan API trước khi adapter thật của T2/T3/T4 xong.
T1-05 sẽ thay các lớp này bằng adapter thật trong main.py.
Mỗi stub ghi tên lời gọi vào `calls` dùng chung để test kiểm tra thứ tự xử lý.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from datetime import datetime, timezone
from typing import Optional, Sequence

from .ports import (
    DeviceEntry, DeviceKeySnapshot, Direction, FreshnessDecision, GateAck, IClock,
    IOutboxRepository, IQrVerifier, IReplayGuard, ISnapshotFreshnessPolicy, ISnapshotStore,
    ITurnstileGateway, OutboxRecord, QrPayload, ReasonCode, SnapshotTier, TxStatus,
    VerifyOutcome,
)


class FakeClock(IClock):
    def __init__(self, now: Optional[datetime] = None) -> None:
        self.current = now or datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current


def encode_qr(v: int, did: str, iat: int, seq: int, sig: bytes = b"sig") -> bytes:
    """Mã hóa QR dạng JSON giống fake_mobile (sig ở base64)."""
    return json.dumps(
        {"v": v, "did": did, "iat": iat, "seq": seq, "sig": base64.b64encode(sig).decode()}
    ).encode()


class StubVerifier(IQrVerifier):
    """Decode JSON thật; verify trả `outcome` cấu hình sẵn (mặc định hợp lệ)."""

    def __init__(self, calls: list[str], outcome: Optional[VerifyOutcome] = None) -> None:
        self.calls = calls
        self.outcome = outcome or VerifyOutcome(ok=True)

    @property
    def version(self) -> int:
        return 1

    def decode(self, raw: bytes) -> Optional[QrPayload]:
        self.calls.append("decode")
        try:
            d = json.loads(raw)
            return QrPayload(int(d["v"]), str(d["did"]), int(d["iat"]), int(d["seq"]),
                             base64.b64decode(d["sig"]))
        except (ValueError, KeyError, TypeError):
            return None

    def verify(self, payload, snapshot, now) -> VerifyOutcome:
        self.calls.append("verify")
        return self.outcome


class StubSnapshotStore(ISnapshotStore):
    def __init__(self, issued_at: Optional[datetime] = None) -> None:
        self.snapshot = DeviceKeySnapshot(
            version=1,
            issued_at=issued_at or datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc),
            signing_kid="dev", devices=(DeviceEntry("dev-001", "EMP-001", ""),),
            revoked_employees=frozenset(), sig=b"",
        )

    def current(self):
        return self.snapshot

    def load(self, raw: bytes) -> bool:
        return False

    def lookup(self, did: str):
        return self.snapshot.find(did)

    def is_revoked(self, did: str) -> bool:
        return False


class StubFreshnessPolicy(ISnapshotFreshnessPolicy):
    """Trả quyết định cấu hình theo chiều; mặc định FRESH cho mọi chiều."""

    def __init__(self, calls: list[str], decisions: Optional[dict] = None) -> None:
        self.calls = calls
        self.decisions = decisions or {}

    def evaluate(self, snapshot, direction: Direction, now) -> FreshnessDecision:
        self.calls.append(f"freshness:{direction.value}")
        return self.decisions.get(direction, FreshnessDecision(tier=SnapshotTier.FRESH, allow=True))


class InMemoryReplayGuard(IReplayGuard):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.watermarks: dict[str, int] = {}
        self.floor_epoch = 0

    def check(self, did: str, seq: int, iat: int) -> Optional[ReasonCode]:
        self.calls.append("replay.check")
        if seq <= self.watermarks.get(did, -1) or iat < self.floor_epoch:
            return ReasonCode.REPLAY
        return None

    def commit(self, did: str, seq: int) -> None:
        self.calls.append("replay.commit")
        self.watermarks[did] = max(seq, self.watermarks.get(did, -1))

    def set_floor(self, ts: datetime) -> None:
        self.floor_epoch = int(ts.timestamp())


class InMemoryOutbox(IOutboxRepository):
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.rows: dict[str, OutboxRecord] = {}
        self._seq = 0

    def _set(self, event_id: str, **kw) -> bool:
        if event_id not in self.rows:
            return False
        self.rows[event_id] = dataclasses.replace(self.rows[event_id], **kw)
        return True

    def find(self, event_id):
        self.calls.append("outbox.find")
        return self.rows.get(event_id)

    def insert_received(self, record):
        self.calls.append("outbox.insert_received")
        if record.event_id not in self.rows:
            self._seq += 1
            self.rows[record.event_id] = dataclasses.replace(record, station_seq=self._seq)
        return self.rows[record.event_id]

    def mark_command_sent(self, event_id, at):
        self.calls.append("outbox.mark_command_sent")
        return self._set(event_id, command_sent_at=at)

    def mark_opened(self, event_id, at):
        self.calls.append("outbox.mark_opened")
        return self._set(event_id, status=TxStatus.OPENED, ack_at=at)

    def mark_unconfirmed(self, event_id, at):
        self.calls.append("outbox.mark_unconfirmed")
        return self._set(event_id, status=TxStatus.UNCONFIRMED)

    def mark_failed(self, event_id, at):
        self.calls.append("outbox.mark_failed")
        return self._set(event_id, status=TxStatus.FAILED)

    def list_pending_recovery(self) -> Sequence[OutboxRecord]:
        return [r for r in self.rows.values() if r.status == TxStatus.RECEIVED]

    def list_unsynced(self, limit: int = 100) -> Sequence[OutboxRecord]:
        return [r for r in self.rows.values()
                if r.status != TxStatus.RECEIVED and not r.synced][:limit]

    def mark_synced(self, event_ids) -> None:
        for e in event_ids:
            self._set(e, synced=True)


class StubTurnstile(ITurnstileGateway):
    def __init__(self, calls: list[str], ack: GateAck = GateAck.OPENED) -> None:
        self.calls = calls
        self.ack = ack
        self.commands: list[str] = []

    def open_gate(self, station_id, gate_id, direction, command_id, deadline) -> GateAck:
        self.calls.append("turnstile.open_gate")
        self.commands.append(command_id)
        return self.ack

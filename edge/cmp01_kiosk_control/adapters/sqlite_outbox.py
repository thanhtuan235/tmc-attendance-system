"""SqliteWalOutbox – cài IOutboxRepository trên edge_tx.db (T4-01, ADR-010, Δ-C1.6).

SQLite WAL + synchronous=FULL: mỗi transaction đã commit thì còn sau khi mất điện.
Hàm mark_command_sent chính là DispatchMarkerStore: dấu mốc ghi bền TRƯỚC khi
gọi turnstile. Theo R9, chỉ lớp này ghi file edge_tx.db.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Sequence

from edge.cmp01_kiosk_control.ports import (
    CredentialType, Direction, IOutboxRepository, OutboxRecord, ReasonCode,
    SnapshotTier, TxStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    eventId        TEXT PRIMARY KEY,
    qrHash         TEXT NOT NULL,
    stationId      TEXT NOT NULL,
    gateId         TEXT NOT NULL,
    stationSeq     INTEGER NOT NULL,
    did            TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    direction      TEXT NOT NULL,
    credentialType TEXT NOT NULL,
    status         TEXT NOT NULL,
    reasonCode     TEXT,
    snapshotTier   TEXT NOT NULL,
    needsReview    INTEGER NOT NULL DEFAULT 0,
    createdAt      TEXT NOT NULL,
    commandSentAt  TEXT,
    ackAt          TEXT,
    synced         INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_station_seq ON outbox(stationId, stationSeq);
CREATE INDEX IF NOT EXISTS ix_outbox_did_seq ON outbox(did, seq);  -- KHÔNG duy nhất (ADR-014)
CREATE INDEX IF NOT EXISTS ix_outbox_status_synced ON outbox(status, synced);
"""

_FINAL = (TxStatus.OPENED.value, TxStatus.UNCONFIRMED.value, TxStatus.FAILED.value)


def _dt(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value is not None else None


def _to_record(row: sqlite3.Row) -> OutboxRecord:
    return OutboxRecord(
        event_id=row["eventId"],
        qr_hash=row["qrHash"],
        station_id=row["stationId"],
        gate_id=row["gateId"],
        did=row["did"],
        seq=row["seq"],
        direction=Direction(row["direction"]),
        credential_type=CredentialType(row["credentialType"]),
        status=TxStatus(row["status"]),
        snapshot_tier=SnapshotTier(row["snapshotTier"]),
        created_at=_parse_dt(row["createdAt"]),
        station_seq=row["stationSeq"],
        reason_code=ReasonCode(row["reasonCode"]) if row["reasonCode"] else None,
        needs_review=bool(row["needsReview"]),
        command_sent_at=_parse_dt(row["commandSentAt"]),
        ack_at=_parse_dt(row["ackAt"]),
        synced=bool(row["synced"]),
    )


class SqliteWalOutbox(IOutboxRepository):
    def __init__(self, db_path: str | Path):
        self._lock = threading.RLock()
        # isolation_level=None: tự quản lý transaction bằng BEGIN IMMEDIATE.
        self._con = sqlite3.connect(
            str(db_path), isolation_level=None, check_same_thread=False
        )
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=FULL")
        self._con.executescript(_SCHEMA)

    # ------------------------------------------------------------ tiện ích

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def pragma(self, name: str):
        """Đọc một PRAGMA (dùng cho kiểm thử và chẩn đoán)."""
        with self._lock:
            return self._con.execute(f"PRAGMA {name}").fetchone()[0]

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Một transaction ghi: BEGIN IMMEDIATE → COMMIT, lỗi thì ROLLBACK."""
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                yield self._con
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            else:
                self._con.execute("COMMIT")

    def _find(self, con: sqlite3.Connection, event_id: str) -> Optional[OutboxRecord]:
        row = con.execute("SELECT * FROM outbox WHERE eventId = ?", (event_id,)).fetchone()
        return _to_record(row) if row else None

    # ------------------------------------------------------------ IOutboxRepository

    def find(self, event_id: str) -> Optional[OutboxRecord]:
        with self._lock:
            return self._find(self._con, event_id)

    def insert_received(self, record: OutboxRecord) -> OutboxRecord:
        with self._tx() as con:
            existing = self._find(con, record.event_id)
            if existing is not None:          # idempotent theo eventId
                return existing
            nxt = con.execute(
                "SELECT COALESCE(MAX(stationSeq), 0) + 1 FROM outbox WHERE stationId = ?",
                (record.station_id,),
            ).fetchone()[0]
            con.execute(
                """INSERT INTO outbox (eventId, qrHash, stationId, gateId, stationSeq, did, seq,
                       direction, credentialType, status, reasonCode, snapshotTier, needsReview,
                       createdAt, commandSentAt, ackAt, synced)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.event_id, record.qr_hash, record.station_id, record.gate_id, nxt,
                    record.did, record.seq, record.direction.value,
                    record.credential_type.value, TxStatus.RECEIVED.value,
                    record.reason_code.value if record.reason_code else None,
                    record.snapshot_tier.value, int(record.needs_review),
                    _dt(record.created_at), None, None, 0,
                ),
            )
            return self._find(con, record.event_id)  # type: ignore[return-value]

    def mark_command_sent(self, event_id: str, at: datetime) -> bool:
        """Dấu mốc gửi lệnh: chỉ ghi khi còn RECEIVED và chưa có dấu mốc."""
        with self._tx() as con:
            if self._find(con, event_id) is None:
                return False
            con.execute(
                "UPDATE outbox SET commandSentAt = ? "
                "WHERE eventId = ? AND status = 'RECEIVED' AND commandSentAt IS NULL",
                (_dt(at), event_id),
            )
            return True

    def _mark_final(self, event_id: str, status: TxStatus, at: datetime) -> bool:
        """Chỉ chuyển từ RECEIVED; đã có kết quả cuối thì giữ nguyên (append-only)."""
        with self._tx() as con:
            if self._find(con, event_id) is None:
                return False
            con.execute(
                "UPDATE outbox SET status = ?, ackAt = ? WHERE eventId = ? AND status = 'RECEIVED'",
                (status.value, _dt(at), event_id),
            )
            return True

    def mark_opened(self, event_id: str, at: datetime) -> bool:
        return self._mark_final(event_id, TxStatus.OPENED, at)

    def mark_unconfirmed(self, event_id: str, at: datetime) -> bool:
        return self._mark_final(event_id, TxStatus.UNCONFIRMED, at)

    def mark_failed(self, event_id: str, at: datetime) -> bool:
        return self._mark_final(event_id, TxStatus.FAILED, at)

    def list_pending_recovery(self) -> Sequence[OutboxRecord]:
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM outbox WHERE status = 'RECEIVED' ORDER BY createdAt, stationSeq"
            ).fetchall()
        return [_to_record(r) for r in rows]

    def list_unsynced(self, limit: int = 100) -> Sequence[OutboxRecord]:
        marks = ",".join("?" * len(_FINAL))
        with self._lock:
            rows = self._con.execute(
                f"SELECT * FROM outbox WHERE synced = 0 AND status IN ({marks}) "
                "ORDER BY stationId, stationSeq LIMIT ?",
                (*_FINAL, limit),
            ).fetchall()
        return [_to_record(r) for r in rows]

    def mark_synced(self, event_ids: Sequence[str]) -> None:
        if not event_ids:
            return
        with self._tx() as con:
            con.executemany(
                "UPDATE outbox SET synced = 1 WHERE eventId = ?", [(e,) for e in event_ids]
            )

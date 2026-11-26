"""KioskGateOrchestrator – thành phần 2 của CMP-01 (T1-03).

Cài IGateScan theo đúng thứ tự Hình 3.1 / ADR-013 (docstring của IGateScan):

  1  decode                              -> MALFORMED
  1b eventId + qrHash khớp outbox        -> trả lại kết quả cũ, không gửi lệnh mới
  2-4 IQrVerifier.verify                 -> QR_EXPIRED / FUTURE / CLOCK_SKEW /
                                            UNKNOWN_DEVICE / DEVICE_REVOKED / BAD_SIGNATURE
  5  ISnapshotFreshnessPolicy            -> STALE_SNAPSHOT (chiều vào)
  6  chính sách trạm/nhà máy             -> POLICY_DENIED (chưa có port, Task 2 luôn cho qua)
  7  IReplayGuard.check                  -> REPLAY
  Chấp nhận: insert_received -> ReplayGuard.commit -> mark_command_sent
             -> open_gate(deadline) -> mark_opened / mark_unconfirmed / mark_failed

Quy tắc DIP (R1): file này chỉ import từ ports/ và config, không import adapters.
Lượt bị từ chối không ghi outbox, chỉ ghi log audit. Không ném ngoại lệ ra Kiosk.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Optional, Sequence

from ..config import EdgeConfig
from ..ports import (
    CredentialType,
    GateAck,
    IClock,
    IGateScan,
    IOutboxRepository,
    IQrVerifier,
    IReplayGuard,
    ISnapshotFreshnessPolicy,
    ISnapshotStore,
    ITurnstileGateway,
    OutboxRecord,
    QrPayload,
    ReasonCode,
    ScanRequest,
    ScanResult,
    ScanStatus,
    SnapshotTier,
    TxStatus,
)

audit_log = logging.getLogger("cmp01.audit")
log = logging.getLogger(__name__)

_ACK_TO_STATUS = {
    GateAck.OPENED: TxStatus.OPENED,
    GateAck.UNCONFIRMED: TxStatus.UNCONFIRMED,
    GateAck.FAILED: TxStatus.FAILED,
}


def compute_event_id(station_id: str, did: str, seq: int) -> str:
    """eventId = sha256(stationId|did|seq) (hex)."""
    return hashlib.sha256(f"{station_id}|{did}|{seq}".encode("utf-8")).hexdigest()


def compute_qr_hash(payload: QrPayload) -> str:
    """qrHash = sha256(payload ‖ sig), payload ở dạng canonical()."""
    return hashlib.sha256(payload.canonical() + payload.sig).hexdigest()


class KioskGateOrchestrator(IGateScan):
    def __init__(
        self,
        *,
        verifiers: Sequence[IQrVerifier],
        snapshot_store: ISnapshotStore,
        freshness_policy: ISnapshotFreshnessPolicy,
        replay_guard: IReplayGuard,
        outbox: IOutboxRepository,
        turnstile: ITurnstileGateway,
        clock: IClock,
        config: EdgeConfig,
    ) -> None:
        if not verifiers:
            raise ValueError("Cần ít nhất một IQrVerifier")
        self._verifiers = {v.version: v for v in verifiers}
        self._snapshots = snapshot_store
        self._freshness = freshness_policy
        self._replay = replay_guard
        self._outbox = outbox
        self._turnstile = turnstile
        self._clock = clock
        self._cfg = config

    # ------------------------------------------------------------ IGateScan

    def submit_scan(self, req: ScanRequest) -> ScanResult:
        try:
            return self._process(req)
        except Exception:  # hợp đồng: không ném ngoại lệ ra Kiosk
            log.exception("Lỗi không mong đợi khi xử lý lượt quét tại %s", req.station_id)
            return ScanResult(status=ScanStatus.REJECTED, reason_code=None)

    # ------------------------------------------------------------ luồng chính

    def _process(self, req: ScanRequest) -> ScanResult:
        started = self._clock.now()

        # Bước 1: decode
        decoded = self._decode(req.raw_qr)
        if decoded is None:
            return self._reject(req, ReasonCode.MALFORMED)
        verifier, payload = decoded

        # Bước 1b: idempotency theo eventId + qrHash
        event_id = compute_event_id(req.station_id, payload.did, payload.seq)
        qr_hash = compute_qr_hash(payload)
        existing = self._outbox.find(event_id)
        if existing is not None and existing.qr_hash == qr_hash:
            return self._result_from_record(existing, replayed=True)
        # Khớp eventId nhưng khác qrHash: không tin, đi tiếp để ReplayGuard chặn.

        # Bước 2-4: xác minh QR
        snapshot = self._snapshots.current()
        outcome = verifier.verify(payload, snapshot, started)
        if not outcome.ok:
            return self._reject(req, outcome.reason_code or ReasonCode.MALFORMED, payload, event_id)

        # Bước 5: độ cũ snapshot theo chiều
        fresh = self._freshness.evaluate(snapshot, req.direction, started)
        if not fresh.allow:
            return self._reject(req, fresh.reason_code or ReasonCode.STALE_SNAPSHOT, payload, event_id)

        # Bước 6: chính sách trạm/nhà máy – chưa có port trong v0.1-interfaces.

        # Bước 7: ReplayGuard (chỉ sau khi chữ ký hợp lệ)
        replay = self._replay.check(payload.did, payload.seq, payload.iat)
        if replay is not None:
            return self._reject(req, replay, payload, event_id)

        # Chấp nhận: ghi outbox RECEIVED trước khi làm gì với cổng
        record = self._outbox.insert_received(
            OutboxRecord(
                event_id=event_id,
                qr_hash=qr_hash,
                station_id=req.station_id,
                gate_id=req.gate_id,
                did=payload.did,
                seq=payload.seq,
                direction=req.direction,
                credential_type=CredentialType.DEVICE_BOUND,
                status=TxStatus.RECEIVED,
                snapshot_tier=fresh.tier,
                created_at=started,
                needs_review=fresh.needs_review,
            )
        )
        self._replay.commit(payload.did, payload.seq)

        # Dấu mốc gửi lệnh (ADR-010) rồi mới gọi cổng, đúng một lần
        self._outbox.mark_command_sent(event_id, self._clock.now())
        deadline = started + timedelta(milliseconds=self._cfg.nfr01_budget_ms)
        ack = self._turnstile.open_gate(
            req.station_id, req.gate_id, req.direction, command_id=event_id, deadline=deadline
        )

        at = self._clock.now()
        tx_status = _ACK_TO_STATUS.get(ack, TxStatus.UNCONFIRMED)
        {
            TxStatus.OPENED: self._outbox.mark_opened,
            TxStatus.UNCONFIRMED: self._outbox.mark_unconfirmed,
            TxStatus.FAILED: self._outbox.mark_failed,
        }[tx_status](event_id, at)

        return ScanResult(
            status=ScanStatus.ACCEPTED,
            event_id=record.event_id,
            tx_status=tx_status,
            stale_snapshot=fresh.stale_snapshot,
            needs_review=fresh.needs_review,
        )

    # ------------------------------------------------------------ hỗ trợ

    def _decode(self, raw: bytes) -> Optional[tuple[IQrVerifier, QrPayload]]:
        """Thử lần lượt các verifier; chọn verifier khớp trường v của payload."""
        for verifier in self._verifiers.values():
            try:
                payload = verifier.decode(raw)
            except Exception:
                payload = None
            if payload is not None:
                chosen = self._verifiers.get(payload.v)
                return (chosen, payload) if chosen is not None else None
        return None

    @staticmethod
    def _result_from_record(rec: OutboxRecord, *, replayed: bool) -> ScanResult:
        return ScanResult(
            status=ScanStatus.ACCEPTED,
            event_id=rec.event_id,
            tx_status=rec.status,
            stale_snapshot=rec.snapshot_tier == SnapshotTier.STALE,
            needs_review=rec.needs_review,
            replayed_result=replayed,
        )

    @staticmethod
    def _reject(
        req: ScanRequest,
        reason: ReasonCode,
        payload: Optional[QrPayload] = None,
        event_id: Optional[str] = None,
    ) -> ScanResult:
        audit_log.info(
            "SCAN_REJECTED station=%s gate=%s dir=%s did=%s seq=%s reason=%s",
            req.station_id, req.gate_id, req.direction.value,
            payload.did if payload else None, payload.seq if payload else None, reason.value,
        )
        return ScanResult(status=ScanStatus.REJECTED, reason_code=reason, event_id=event_id)

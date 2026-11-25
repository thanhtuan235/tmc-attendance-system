"""Sync Agent (OutboxRelay) – thành phần 9 của CMP-01 (T4-04, ADR vấn đề 1).

Chạy nền: định kỳ lấy bản ghi đã có trạng thái cuối nhưng chưa đồng bộ trong outbox,
gửi lô lên CMP-03 qua ICloudEventClient; thành công thì mark_synced, thất bại thì giữ
nguyên và lùi thời gian chờ (exponential backoff, có trần) rồi thử lại.
Giao nhận at-least-once; CMP-03 chống trùng theo event_id nên gửi lặp không sinh bản ghi trùng.

Chỉ phụ thuộc port (IOutboxRepository, ICloudEventClient) – DIP.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from packages.contracts import AttendanceEvent

from ..ports import ICloudEventClient, IOutboxRepository, OutboxRecord

log = logging.getLogger("cmp01.sync")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def to_event(rec: OutboxRecord) -> AttendanceEvent:
    """Ánh xạ bản ghi outbox sang Published Language (packages/contracts)."""
    return AttendanceEvent(
        event_id=rec.event_id,
        station_id=rec.station_id,
        station_seq=rec.station_seq or 0,
        gate_id=rec.gate_id,
        did=rec.did,
        seq=rec.seq,
        direction=rec.direction.value,
        status=rec.status.value,
        credential_type=rec.credential_type.value,
        snapshot_tier=rec.snapshot_tier.value,
        needs_review=rec.needs_review,
        occurred_at=_iso(rec.created_at),
        ack_at=_iso(rec.ack_at),
        reason_code=rec.reason_code.value if rec.reason_code else None,
    )


class SyncAgent:
    def __init__(
        self,
        outbox: IOutboxRepository,
        cloud: ICloudEventClient,
        *,
        interval_sec: float = 3.0,
        batch_size: int = 100,
        max_backoff_sec: float = 30.0,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self._outbox = outbox
        self._cloud = cloud
        self._interval = interval_sec
        self._batch = batch_size
        self._max_backoff = max_backoff_sec
        self._on_error = on_error
        self._failures = 0
        self._last_ok_at: Optional[datetime] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ một vòng

    def run_once(self) -> int:
        """Đẩy tối đa các lô đang chờ. Trả số bản ghi đã đồng bộ; ném lỗi nếu Cloud lỗi."""
        synced = 0
        while True:
            pending = list(self._outbox.list_unsynced(self._batch))
            if not pending:
                break
            if not self._cloud.send_batch([to_event(r) for r in pending]):
                self._failures += 1
                raise ConnectionError("CMP-03 chưa nhận lô")
            self._outbox.mark_synced([r.event_id for r in pending])
            synced += len(pending)
            if len(pending) < self._batch:
                break
        self._failures = 0
        if synced:
            log.info("Đã đồng bộ %d bản ghi lên CMP-03", synced)
        return synced

    def next_delay(self) -> float:
        """Khoảng chờ tới vòng sau: interval khi ổn định, nhân đôi theo số lần lỗi liên tiếp."""
        if self._failures == 0:
            return self._interval
        return min(self._interval * (2 ** self._failures), self._max_backoff)

    # ------------------------------------------------------------ chạy nền

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 – không để luồng nền chết
                if self._on_error:
                    self._on_error(exc)
            self._stop.wait(self.next_delay())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cmp01-sync-agent", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------ trạng thái

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def pending_count(self) -> int:
        return len(self._outbox.list_unsynced(10_000))

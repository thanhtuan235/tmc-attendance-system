"""Recovery khi khởi động Edge – thành phần 8 của CMP-01 (T4-03, ADR-010).

Chạy một lần trước khi Edge nhận lượt quét mới:
1. Đặt sàn replay = thời điểm khởi động. ReplayGuard giữ watermark trong bộ nhớ nên mất khi
   khởi động lại; sàn này chặn QR có iat trước lúc khởi động bị dùng lại.
2. Duyệt bản ghi còn RECEIVED (chưa có kết quả cuối):
   - đã có dấu mốc commandSentAt → UNCONFIRMED (không biết cổng đã mở hay chưa, KHÔNG gửi lại
     lệnh mở – No Blind Retry, cần bảo vệ xác nhận);
   - chưa gửi lệnh → FAILED (chắc chắn cổng chưa mở).
Sau đó Sync Agent đẩy các trạng thái cuối này lên CMP-03.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..ports import IClock, IOutboxRepository, IReplayGuard

log = logging.getLogger("cmp01.recovery")


@dataclass(frozen=True)
class RecoveryReport:
    unconfirmed: int
    failed: int
    replay_floor: str


class StartupRecovery:
    def __init__(self, outbox: IOutboxRepository, replay_guard: IReplayGuard, clock: IClock) -> None:
        self._outbox = outbox
        self._replay = replay_guard
        self._clock = clock

    def run(self) -> RecoveryReport:
        now = self._clock.now()
        self._replay.set_floor(now)
        unconfirmed = failed = 0
        for rec in self._outbox.list_pending_recovery():
            if rec.command_sent_at is not None:
                self._outbox.mark_unconfirmed(rec.event_id, now)
                unconfirmed += 1
            else:
                self._outbox.mark_failed(rec.event_id, now)
                failed += 1
        report = RecoveryReport(unconfirmed, failed, now.isoformat())
        log.info("Recovery: %d UNCONFIRMED, %d FAILED, sàn replay %s",
                 unconfirmed, failed, report.replay_floor)
        return report

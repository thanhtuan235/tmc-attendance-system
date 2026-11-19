"""ICloudEventClient – gửi lô sự kiện lên CMP-03 (at-least-once)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from packages.contracts import AttendanceEvent


class ICloudEventClient(ABC):
    @abstractmethod
    def send_batch(self, events: Sequence[AttendanceEvent]) -> bool:
        """POST /events (docs/contracts/cmp03_events.md).
        True khi CMP-03 trả 200 (kể cả bản ghi trùng); False khi lỗi kết nối hoặc 5xx.
        Không ném ngoại lệ; OutboxRelay tự backoff và thử lại."""
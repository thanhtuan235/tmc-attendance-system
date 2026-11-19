"""IReplayGuard – chống dùng lại QR (ADR-013, thay cache jti của ADR-003)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from .dto import ReasonCode


class IReplayGuard(ABC):
    """Giữ watermark seq lớn nhất đã chấp nhận theo did (Task 2: dict thay Redis).

    Chỉ được gọi SAU khi chữ ký hợp lệ (bước 7), để kẻ tấn công không làm đầy
    bộ nhớ bằng QR giả (T-11). Yêu cầu gửi lại khớp eventId + qrHash đã được
    trả ở bước 1b nên không tới đây.
    """

    @abstractmethod
    def check(self, did: str, seq: int, iat: int) -> Optional[ReasonCode]:
        """iat là epoch giây (cùng đơn vị QrPayload.iat).
        Trả REPLAY nếu seq <= watermark(did) hoặc iat < sàn replay; ngược lại None.
        Không thay đổi trạng thái."""

    @abstractmethod
    def commit(self, did: str, seq: int) -> None:
        """Nâng watermark(did) lên seq. Gọi ngay sau check() trả None."""

    @abstractmethod
    def set_floor(self, ts: datetime) -> None:
        """Đặt sàn replay (khi Edge khởi động lại / Standby tiếp quản)."""
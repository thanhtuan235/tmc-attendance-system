"""IQrVerifier – bộ xác minh QR theo loại credential (Strategy, VP-1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from .dto import DeviceKeySnapshot, QrPayload, VerifyOutcome


class IQrVerifier(ABC):
    """Hợp đồng LSP (giữ từ QrAccessPolicyVerifier của M1):
    - không trả None (trừ decode khi sai định dạng), không ném ngoại lệ chưa kiểm soát;
    - kết quả bất biến;
    - P95 <= 150 ms (NFR-08).
    QrVerifierSelector chọn cài đặt theo trường v của payload.
    """

    @property
    @abstractmethod
    def version(self) -> int:
        """Giá trị v mà verifier này xử lý (1 = DEVICE_BOUND)."""

    @abstractmethod
    def decode(self, raw: bytes) -> Optional[QrPayload]:
        """Bước 1: giải mã payload. Sai định dạng trả None (orchestrator ghi MALFORMED)."""

    @abstractmethod
    def verify(
        self,
        payload: QrPayload,
        snapshot: Optional[DeviceKeySnapshot],
        now: datetime,
    ) -> VerifyOutcome:
        """Bước 2–4 theo đúng thứ tự:
        2. iat ngoài [now - qr.windowPastSec, now + qr.windowFutureSec] -> QR_EXPIRED / FUTURE
        3. did không có trong snapshot -> UNKNOWN_DEVICE;
           thiết bị hoặc employeeRef bị thu hồi -> DEVICE_REVOKED
        4. chữ ký trên payload.canonical() sai -> BAD_SIGNATURE
        snapshot = None: bước 3 không thực hiện được -> UNKNOWN_DEVICE.
        """
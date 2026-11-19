"""IGateScan – interface cung cấp của CMP-01 cho Kiosk (giữ nguyên từ M1)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .dto import ScanRequest, ScanResult


class IGateScan(ABC):
    """Nhận một lượt quét và trả kết quả trong ngân sách NFR-01 (P95 <= 2 s).

    Thứ tự xử lý bắt buộc (Hình 3.1, ADR-013):
      1  decode, kiểm định dạng                       -> MALFORMED
      1b eventId = sha256(stationId|did|seq), qrHash = sha256(payload‖sig);
         khớp CẢ HAI trong outbox -> trả lại kết quả cũ, không gửi lệnh mới
      2  cửa sổ iat                                   -> QR_EXPIRED / FUTURE / CLOCK_SKEW
      3  tra did, thu hồi                             -> UNKNOWN_DEVICE / DEVICE_REVOKED
      4  chữ ký ECDSA P-256                           -> BAD_SIGNATURE
      5  độ cũ snapshot theo chiều                    -> STALE_SNAPSHOT (chiều vào)
      6  chính sách trạm/nhà máy                      -> POLICY_DENIED
      7  ReplayGuard                                  -> REPLAY
    Chấp nhận -> ghi outbox RECEIVED -> ghi dấu mốc -> open_gate -> ghi kết quả.
    Lượt bị từ chối không ghi outbox, chỉ ghi AuditEvent.
    Không ném ngoại lệ ra Kiosk.
    """

    @abstractmethod
    def submit_scan(self, req: ScanRequest) -> ScanResult: ...
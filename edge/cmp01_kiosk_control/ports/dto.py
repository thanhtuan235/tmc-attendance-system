"""DTO và enum dùng chung của CMP-01 (T1-02).

Quy tắc R1 (Bảng 4.6): package ports chỉ chứa interface và DTO, không chứa logic
nghiệp vụ, không phụ thuộc domain hay adapters. Mọi DTO là frozen dataclass để
thỏa hợp đồng LSP của QrAccessPolicyVerifier: kết quả bất biến.
Mọi thời điểm là datetime có múi giờ UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------- Enum

class ReasonCode(str, Enum):
    """Mã lý do từ chối ổn định theo ADR-013 (Hình 3.1)."""

    MALFORMED = "MALFORMED"            # bước 1: sai định dạng / phiên bản v
    QR_EXPIRED = "QR_EXPIRED"          # bước 2: iat < now - qr.windowPastSec
    FUTURE = "FUTURE"                  # bước 2: iat > now + qr.windowFutureSec
    CLOCK_SKEW = "CLOCK_SKEW"          # bước 2: lệch đồng hồ quá clock.maxSkewSec
    UNKNOWN_DEVICE = "UNKNOWN_DEVICE"  # bước 3: did không có trong snapshot
    DEVICE_REVOKED = "DEVICE_REVOKED"  # bước 3: thiết bị hoặc nhân viên bị thu hồi
    BAD_SIGNATURE = "BAD_SIGNATURE"    # bước 4: chữ ký ECDSA P-256 sai
    STALE_SNAPSHOT = "STALE_SNAPSHOT"  # bước 5: snapshot EXPIRED ở chiều vào
    POLICY_DENIED = "POLICY_DENIED"    # bước 6: chính sách trạm/nhà máy
    REPLAY = "REPLAY"                  # bước 7: seq <= watermark hoặc iat < sàn replay


class Direction(str, Enum):
    IN = "IN"
    OUT = "OUT"


class CredentialType(str, Enum):
    """Loại credential (Δ-Q2.11). Task 2 chỉ hiện thực DEVICE_BOUND."""

    DEVICE_BOUND = "DEVICE_BOUND"
    WEB_TOKEN = "WEB_TOKEN"


class SnapshotTier(str, Enum):
    """Mức độ cũ của snapshot theo Bảng 3.4."""

    FRESH = "FRESH"      # A <= snapshot.freshMin (5 phút)
    STALE = "STALE"      # 5 phút < A <= snapshot.entryMaxHours (4 giờ)
    EXPIRED = "EXPIRED"  # A > 4 giờ hoặc không có snapshot hợp lệ


class TxStatus(str, Enum):
    """Trạng thái giao dịch mở cổng trong outbox (Bảng 2.18, ADR-010)."""

    RECEIVED = "RECEIVED"
    OPENED = "OPENED"
    UNCONFIRMED = "UNCONFIRMED"
    FAILED = "FAILED"


class GateAck(str, Enum):
    """Ba kết quả duy nhất của ITurnstileGateway.open_gate (ADR-008)."""

    OPENED = "OPENED"
    UNCONFIRMED = "UNCONFIRMED"  # hết deadline: lệnh có thể đã chạy, KHÔNG phải FAILED
    FAILED = "FAILED"


class ScanStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------- QR

@dataclass(frozen=True)
class QrPayload:
    """Payload QR sinh trên thiết bị: {v, did, iat, seq, sig} (ADR-012)."""

    v: int
    did: str
    iat: int      # epoch giây
    seq: int      # bộ đếm tăng đơn điệu theo thiết bị
    sig: bytes    # chữ ký ECDSA P-256 (DER) trên canonical()

    def canonical(self) -> bytes:
        """Chuỗi chuẩn hóa để ký và kiểm chữ ký: "v|did|iat|seq"."""
        return f"{self.v}|{self.did}|{self.iat}|{self.seq}".encode("utf-8")


@dataclass(frozen=True)
class ScanRequest:
    """Yêu cầu quét từ Kiosk qua IGateScan."""

    station_id: str
    gate_id: str
    direction: Direction
    raw_qr: bytes


@dataclass(frozen=True)
class ScanResult:
    """Kết quả trả về Kiosk."""

    status: ScanStatus
    reason_code: Optional[ReasonCode] = None
    event_id: Optional[str] = None
    tx_status: Optional[TxStatus] = None
    stale_snapshot: bool = False
    needs_review: bool = False
    replayed_result: bool = False  # True khi trả lại kết quả cũ ở bước 1b


# ---------------------------------------------------------------- Snapshot

@dataclass(frozen=True)
class DeviceEntry:
    """Một thiết bị trong DeviceKeySnapshot (Bảng 4.17)."""

    did: str
    employee_ref: str
    public_key_pem: str
    status: str = "ACTIVE"  # ACTIVE | REVOKED


@dataclass(frozen=True)
class DeviceKeySnapshot:
    """Snapshot khóa/thiết bị do CMP-02 ký; Edge giữ bản sao chỉ đọc."""

    version: int
    issued_at: datetime
    signing_kid: str
    devices: tuple[DeviceEntry, ...]
    revoked_employees: frozenset[str]
    sig: bytes

    def find(self, did: str) -> Optional[DeviceEntry]:
        """Tra did; trả None nếu không có."""
        for d in self.devices:
            if d.did == did:
                return d
        return None


@dataclass(frozen=True)
class VerifyOutcome:
    """Kết quả bước 2–4 (cửa sổ iat, tra did/thu hồi, chữ ký)."""

    ok: bool
    reason_code: Optional[ReasonCode] = None
    device: Optional[DeviceEntry] = None


@dataclass(frozen=True)
class FreshnessDecision:
    """Kết quả bước 5 theo Bảng 3.4."""

    tier: SnapshotTier
    allow: bool
    stale_snapshot: bool = False
    needs_review: bool = False
    warn: bool = False  # True khi A > snapshot.staleWarnMin
    reason_code: Optional[ReasonCode] = None


# ---------------------------------------------------------------- Outbox

@dataclass(frozen=True)
class OutboxRecord:
    """Một giao dịch trong edge_tx.db (EdgeOutboxRecord + GateCommandMarker)."""

    event_id: str                  # sha256(stationId|did|seq)
    qr_hash: str                   # sha256(payload ‖ sig), dùng ở bước 1b
    station_id: str
    gate_id: str
    did: str
    seq: int
    direction: Direction
    credential_type: CredentialType
    status: TxStatus
    snapshot_tier: SnapshotTier
    created_at: datetime
    station_seq: Optional[int] = None         # outbox cấp khi insert_received
    reason_code: Optional[ReasonCode] = None
    needs_review: bool = False
    command_sent_at: Optional[datetime] = None  # dấu mốc gửi lệnh (ADR-010)
    ack_at: Optional[datetime] = None
    synced: bool = False
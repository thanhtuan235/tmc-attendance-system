"""AttendanceEvent – sự kiện CMP-01 gửi lên CMP-03 qua POST /events.

Trường theo EdgeOutboxRecord và Δ-Q2.11 (credentialType, did/seq, snapshotTier,
mã lý do). Chỉ dùng kiểu cơ bản (str/int/bool) để không phụ thuộc enum của Edge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AttendanceEvent:
    event_id: str                 # khóa chống trùng tại CMP-03
    station_id: str
    station_seq: int              # phát hiện khoảng trống theo trạm (Hình 4.18)
    gate_id: str
    did: Optional[str]
    seq: Optional[int]
    direction: str                # IN | OUT
    status: str                   # OPENED | UNCONFIRMED | FAILED (trạng thái cuối)
    credential_type: str          # DEVICE_BOUND | WEB_TOKEN
    snapshot_tier: str            # FRESH | STALE | EXPIRED
    needs_review: bool
    occurred_at: str              # ISO 8601 UTC, thời điểm ghi RECEIVED tại trạm
    ack_at: Optional[str] = None  # ISO 8601 UTC
    reason_code: Optional[str] = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttendanceEvent":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
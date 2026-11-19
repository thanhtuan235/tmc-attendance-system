"""ISnapshotStore, ISnapshotFreshnessPolicy – snapshot khóa/thiết bị tại Edge."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from .dto import DeviceEntry, DeviceKeySnapshot, Direction, FreshnessDecision


class ISnapshotStore(ABC):
    """Kho snapshot đã ký, bản bền trong edge_cache.db (R9: chỉ adapters.cache ghi)."""

    @abstractmethod
    def current(self) -> Optional[DeviceKeySnapshot]:
        """Snapshot hợp lệ mới nhất; None nếu chưa có."""

    @abstractmethod
    def load(self, raw: bytes) -> bool:
        """Nạp snapshot mới (JSON đã ký).
        Từ chối (trả False) khi sai chữ ký theo khóa gốc ghim
        (runtime-files/pinned_root_pub.pem) hoặc version <= version hiện tại (chống rollback)."""

    @abstractmethod
    def lookup(self, did: str) -> Optional[DeviceEntry]:
        """Tra did trong snapshot hiện tại."""

    @abstractmethod
    def is_revoked(self, did: str) -> bool:
        """True nếu thiết bị bị thu hồi hoặc employeeRef của nó bị thu hồi."""


class ISnapshotFreshnessPolicy(ABC):
    """Nguồn duy nhất quyết định điều kiện xác minh offline (Bảng 3.4, D-02)."""

    @abstractmethod
    def evaluate(
        self,
        snapshot: Optional[DeviceKeySnapshot],
        direction: Direction,
        now: datetime,
    ) -> FreshnessDecision:
        """A = now - snapshot.issued_at.
        FRESH (A <= 5 phút): allow.
        STALE (<= 4 giờ): allow, stale_snapshot=True; warn=True khi A > 15 phút.
        EXPIRED (> 4 giờ) hoặc snapshot None:
          IN  -> allow=False, reason_code=STALE_SNAPSHOT (chuyển bảo vệ);
          OUT -> allow=True, needs_review=True, không tự tính công.
        """
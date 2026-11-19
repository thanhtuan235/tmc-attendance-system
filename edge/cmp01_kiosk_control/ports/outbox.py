"""IOutboxRepository – outbox giao dịch trong edge_tx.db (Δ-C1.6, ADR-010)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence

from .dto import OutboxRecord


class IOutboxRepository(ABC):
    """SQLite WAL, synchronous=FULL. Chỉ cmp01.outbox ghi file này (R9).
    Mỗi hàm mark_* là một transaction; trả False nếu event_id không tồn tại.
    """

    @abstractmethod
    def find(self, event_id: str) -> Optional[OutboxRecord]:
        """Tra theo eventId (bước 1b)."""

    @abstractmethod
    def insert_received(self, record: OutboxRecord) -> OutboxRecord:
        """Ghi bản ghi RECEIVED (mức L1). Cấp station_seq tăng dần theo trạm và
        trả lại bản ghi đã có station_seq. Idempotent theo event_id."""

    @abstractmethod
    def mark_command_sent(self, event_id: str, at: datetime) -> bool:
        """Ghi bền dấu mốc gửi lệnh TRƯỚC khi gọi turnstile (commandId = eventId)."""

    @abstractmethod
    def mark_opened(self, event_id: str, at: datetime) -> bool: ...

    @abstractmethod
    def mark_unconfirmed(self, event_id: str, at: datetime) -> bool: ...

    @abstractmethod
    def mark_failed(self, event_id: str, at: datetime) -> bool: ...

    @abstractmethod
    def list_pending_recovery(self) -> Sequence[OutboxRecord]:
        """Bản ghi chưa có kết quả cuối (RECEIVED, có hoặc chưa có dấu mốc) cho Recovery."""

    @abstractmethod
    def list_unsynced(self, limit: int = 100) -> Sequence[OutboxRecord]:
        """Bản ghi đã có trạng thái cuối nhưng chưa đồng bộ lên CMP-03."""

    @abstractmethod
    def mark_synced(self, event_ids: Sequence[str]) -> None: ...
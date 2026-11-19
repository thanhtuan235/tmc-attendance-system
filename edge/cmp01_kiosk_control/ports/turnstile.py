"""ITurnstileGateway, TurnstileGatewayFactory – cổng xoay (ADR-008, DIP)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .dto import Direction, GateAck


class ITurnstileGateway(ABC):
    """Chi tiết API nhà cung cấp nằm trong adapter, không lọt vào orchestrator."""

    @abstractmethod
    def open_gate(
        self,
        station_id: str,
        gate_id: str,
        direction: Direction,
        command_id: str,
        deadline: datetime,
    ) -> GateAck:
        """Gửi lệnh mở cổng. command_id = eventId.
        Chỉ trả OPENED | UNCONFIRMED | FAILED:
        - hết deadline hoặc không rõ kết quả -> UNCONFIRMED (không phải FAILED);
        - thiết bị từ chối / báo lỗi -> FAILED.
        Không tự gửi lại (No Blind Retry). Không ném ngoại lệ."""


class TurnstileGatewayFactory(ABC):
    """Chọn adapter theo loại thiết bị của trạm (TCP_IP, DRY_CONTACT, FAKE…)."""

    @abstractmethod
    def get_gateway(self, adapter_type: str) -> ITurnstileGateway: ...
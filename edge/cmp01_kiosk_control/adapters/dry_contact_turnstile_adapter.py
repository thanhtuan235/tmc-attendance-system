"""DryContactTurnstileAdapter – khung rỗng minh họa LSP (T4-02, ADR-008).

Xem ghi chú ở TcpIpTurnstileAdapter: giữ đúng hợp đồng, chưa nối thiết bị thật.
"""

from __future__ import annotations

from datetime import datetime

from edge.cmp01_kiosk_control.ports import Direction, GateAck, ITurnstileGateway


class DryContactTurnstileAdapter(ITurnstileGateway):
    def open_gate(
        self,
        station_id: str,
        gate_id: str,
        direction: Direction,
        command_id: str,
        deadline: datetime,
    ) -> GateAck:
        return GateAck.UNCONFIRMED

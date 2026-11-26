"""TcpIpTurnstileAdapter – khung rỗng minh họa LSP (T4-02, ADR-008).

Task 2 chưa nối thiết bị thật. Khung này vẫn giữ đúng hợp đồng ITurnstileGateway
(chỉ trả GateAck, không ném ngoại lệ) nên thay được cho FakeTurnstileAdapter.
Chưa gửi lệnh nào nên trả UNCONFIRMED (không khẳng định FAILED).
"""

from __future__ import annotations

from datetime import datetime

from edge.cmp01_kiosk_control.ports import Direction, GateAck, ITurnstileGateway


class TcpIpTurnstileAdapter(ITurnstileGateway):
    def open_gate(
        self,
        station_id: str,
        gate_id: str,
        direction: Direction,
        command_id: str,
        deadline: datetime,
    ) -> GateAck:
        return GateAck.UNCONFIRMED

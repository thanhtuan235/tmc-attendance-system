"""TurnstileGatewayFactoryImpl – chọn adapter theo loại thiết bị của trạm (T4-02, ADR-008)."""

from __future__ import annotations

from edge.cmp01_kiosk_control.ports import ITurnstileGateway, TurnstileGatewayFactory

from .dry_contact_turnstile_adapter import DryContactTurnstileAdapter
from .fake_turnstile_adapter import FakeTurnstileAdapter
from .tcp_ip_turnstile_adapter import TcpIpTurnstileAdapter


class TurnstileGatewayFactoryImpl(TurnstileGatewayFactory):
    """FAKE được giữ một thể hiện duy nhất để chế độ OK/TIMEOUT/ERROR dùng chung toàn Edge."""

    def __init__(self) -> None:
        self._fake = FakeTurnstileAdapter()

    def get_gateway(self, adapter_type: str) -> ITurnstileGateway:
        kind = str(adapter_type).strip().upper()
        if kind == "FAKE":
            return self._fake
        if kind == "TCP_IP":
            return TcpIpTurnstileAdapter()
        if kind == "DRY_CONTACT":
            return DryContactTurnstileAdapter()
        raise ValueError(f"Loại adapter turnstile không hỗ trợ: {adapter_type!r}")

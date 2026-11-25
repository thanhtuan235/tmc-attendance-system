"""FakeTurnstileAdapter – cổng xoay giả để demo và kiểm thử (T4-02, ADR-008).

Ba chế độ:
- OK      -> OPENED ngay.
- TIMEOUT -> chờ tới hết deadline rồi trả UNCONFIRMED (lệnh có thể đã chạy,
             nên KHÔNG phải FAILED).
- ERROR   -> FAILED (thiết bị từ chối hoặc báo lỗi).
Không tự gửi lại lệnh (No Blind Retry) và không ném ngoại lệ.

Chế độ mặc định lấy từ biến môi trường TMC_TURNSTILE_MODE, đổi lúc chạy qua
set_mode() hoặc endpoint /admin/turnstile-mode (T1 gắn router vào app ở main.py).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from edge.cmp01_kiosk_control.ports import Direction, GateAck, IClock, ITurnstileGateway

MODES = ("OK", "TIMEOUT", "ERROR")


def _normalize(mode: str) -> str:
    value = str(mode).strip().upper()
    if value not in MODES:
        raise ValueError(f"Chế độ turnstile không hợp lệ: {mode!r}; chỉ nhận {MODES}")
    return value


class FakeTurnstileAdapter(ITurnstileGateway):
    def __init__(
        self,
        mode: Optional[str] = None,
        clock: Optional[IClock] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._mode = _normalize(mode or os.environ.get("TMC_TURNSTILE_MODE", "OK"))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self.calls: list[str] = []   # command_id của mỗi lần open_gate (kiểm tra No Blind Retry)

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = _normalize(mode)

    def _now(self) -> datetime:
        return self._clock.now() if self._clock else datetime.now(timezone.utc)

    def open_gate(
        self,
        station_id: str,
        gate_id: str,
        direction: Direction,
        command_id: str,
        deadline: datetime,
    ) -> GateAck:
        try:
            with self._lock:
                self.calls.append(command_id)
            mode = self._mode
            if mode == "ERROR":
                return GateAck.FAILED
            if mode == "TIMEOUT":
                remaining = (deadline - self._now()).total_seconds()
                if remaining > 0:
                    self._sleep(remaining)
                return GateAck.UNCONFIRMED
            return GateAck.OPENED
        except Exception:  # noqa: BLE001 – hợp đồng: không ném ngoại lệ
            # Không rõ kết quả -> UNCONFIRMED, không bao giờ suy ra FAILED.
            return GateAck.UNCONFIRMED


class _ModeBody(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def _valid(cls, v: str) -> str:
        return _normalize(v)


def build_admin_router(adapter: FakeTurnstileAdapter) -> APIRouter:
    """Router /admin/turnstile-mode để đổi chế độ khi demo kịch bản 4."""
    router = APIRouter()

    @router.get("/admin/turnstile-mode")
    def get_mode() -> dict[str, str]:
        return {"mode": adapter.mode}

    @router.put("/admin/turnstile-mode")
    def put_mode(body: _ModeBody) -> dict[str, str]:
        adapter.set_mode(body.mode)
        return {"mode": adapter.mode}

    return router

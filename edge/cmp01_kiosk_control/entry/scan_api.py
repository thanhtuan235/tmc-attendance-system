"""Scan API – thành phần 1 của CMP-01 (T1-03).

POST /scan nhận lượt quét từ Kiosk và chuyển cho IGateScan; GET /health.
Lớp này chỉ chuyển đổi HTTP <-> DTO, không chứa logic nghiệp vụ.
Mọi lượt quét hợp lệ về định dạng HTTP đều trả 200; kết quả nằm ở status/reason_code.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ..ports import Direction, IGateScan, ScanRequest


class ScanIn(BaseModel):
    station_id: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    direction: Direction
    qr: str = Field(description="Nội dung QR thô (JSON hoặc base64 do app sinh)")


class ScanOut(BaseModel):
    status: str
    reason_code: Optional[str] = None
    event_id: Optional[str] = None
    tx_status: Optional[str] = None
    stale_snapshot: bool = False
    needs_review: bool = False
    replayed_result: bool = False


def create_app(gate: IGateScan) -> FastAPI:
    app = FastAPI(title="CMP-01 Kiosk Control – Scan API")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/scan", response_model=ScanOut)
    def scan(body: ScanIn) -> ScanOut:
        result = gate.submit_scan(
            ScanRequest(
                station_id=body.station_id,
                gate_id=body.gate_id,
                direction=body.direction,
                raw_qr=body.qr.encode("utf-8"),
            )
        )
        return ScanOut(
            status=result.status.value,
            reason_code=result.reason_code.value if result.reason_code else None,
            event_id=result.event_id,
            tx_status=result.tx_status.value if result.tx_status else None,
            stale_snapshot=result.stale_snapshot,
            needs_review=result.needs_review,
            replayed_result=result.replayed_result,
        )

    return app

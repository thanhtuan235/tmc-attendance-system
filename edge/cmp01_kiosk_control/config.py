"""Tham số cấu hình của CMP-01 (Bảng 3.5, Δ-Q2.15).

Ở hệ thống thật các giá trị này nằm ở CMP-06; Task 2 đọc mặc định dưới đây và
cho phép ghi đè bằng biến môi trường TMC_<TÊN_TRƯỜNG_VIẾT_HOA>, ví dụ
TMC_SNAPSHOT_ENTRY_MAX_HOURS=1 để demo kịch bản 6 nhanh hơn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class EdgeConfig:
    # S1, S3 – cửa sổ iat của QR thiết bị
    qr_window_past_sec: int = 30
    qr_window_future_sec: int = 5
    # S3, S6 – độ lệch đồng hồ tối đa
    clock_max_skew_sec: int = 5
    # Bảng 3.4 – mức độ cũ snapshot
    snapshot_fresh_min: int = 5
    snapshot_stale_warn_min: int = 15
    snapshot_entry_max_hours: int = 4
    # NFR-01 – ngân sách quét → mở cổng (deadline turnstile = phần còn lại)
    nfr01_budget_ms: int = 2000
    # OutboxRelay (T4-04)
    sync_interval_sec: int = 3
    sync_batch_size: int = 100
    # Địa chỉ CMP-03 mini (T3-04)
    cloud_events_url: str = "http://127.0.0.1:8001/events"


def load_config(env: dict[str, str] | None = None) -> EdgeConfig:
    """Tạo EdgeConfig từ mặc định + biến môi trường TMC_*."""
    env = os.environ if env is None else env
    overrides = {}
    for f in fields(EdgeConfig):
        key = f"TMC_{f.name.upper()}"
        if key in env:
            overrides[f.name] = int(env[key]) if f.type in ("int", int) else env[key]
    return EdgeConfig(**overrides)
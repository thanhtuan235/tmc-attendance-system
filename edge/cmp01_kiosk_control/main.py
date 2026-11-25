"""Composition root của CMP-01 – nơi DUY NHẤT biết lớp cài đặt cụ thể (DIP).

T1-05: lắp adapter thật (DeviceBoundQrVerifier, SnapshotStore, SnapshotFreshnessPolicy,
ReplayGuard, SqliteWalOutbox, TurnstileGatewayFactoryImpl). dev_stubs chỉ còn dùng cho unit test.

Chuẩn bị (một lần):
    python -m tools.fake_mobile --did device_001
    python -m tools.fake_cmp02 --device device_001 employee_001 --version 1
Chạy:
    uvicorn edge.cmp01_kiosk_control.main:build_app --factory --port 8000

Biến môi trường (ngoài các TMC_* của EdgeConfig):
    TMC_RUNTIME_DIR      thư mục chứa edge_tx.db, edge_cache.db, pinned_root_pub.pem (mặc định runtime-files)
    TMC_SNAPSHOT_FILE    snapshot nạp khi khởi động và khi gọi /admin/snapshot/reload (mặc định snapshot.json)
    TMC_TURNSTILE_TYPE   FAKE | TCP_IP | DRY_CONTACT (mặc định FAKE)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .adapters.device_bound_qr_verifier import DeviceBoundQrVerifier
from .adapters.fake_turnstile_adapter import FakeTurnstileAdapter, build_admin_router
from .adapters.replay_guard import ReplayGuard
from .adapters.snapshot_store import SnapshotStore
from .adapters.sqlite_outbox import SqliteWalOutbox
from .adapters.turnstile_gateway_factory import TurnstileGatewayFactoryImpl
from .config import EdgeConfig, load_config
from .entry.scan_api import create_app
from .orchestration.kiosk_gate_orchestrator import KioskGateOrchestrator
from .ports import IClock
from .snapshot_freshness_policy import SnapshotFreshnessPolicy


class SystemClock(IClock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def build_app(config: EdgeConfig | None = None) -> FastAPI:
    config = config or load_config()
    runtime = Path(os.environ.get("TMC_RUNTIME_DIR", "runtime-files"))
    runtime.mkdir(parents=True, exist_ok=True)
    snapshot_file = Path(os.environ.get("TMC_SNAPSHOT_FILE", "snapshot.json"))
    pinned = runtime / "pinned_root_pub.pem"
    if not pinned.exists():
        raise RuntimeError(
            f"Thiếu {pinned}. Chạy trước: python -m tools.fake_cmp02 --device <did> <employee> --version 1"
        )

    clock = SystemClock()
    snapshot_store = SnapshotStore(runtime / "edge_cache.db", pinned)
    outbox = SqliteWalOutbox(runtime / "edge_tx.db")
    replay_guard = ReplayGuard()
    turnstile = TurnstileGatewayFactoryImpl().get_gateway(os.environ.get("TMC_TURNSTILE_TYPE", "FAKE"))

    orchestrator = KioskGateOrchestrator(
        verifiers=[
            DeviceBoundQrVerifier(
                clock,
                window_past_sec=config.qr_window_past_sec,
                window_future_sec=config.qr_window_future_sec,
            )
        ],
        snapshot_store=snapshot_store,
        freshness_policy=SnapshotFreshnessPolicy(config),
        replay_guard=replay_guard,
        outbox=outbox,
        turnstile=turnstile,
        clock=clock,
        config=config,
    )

    app = create_app(orchestrator)
    if isinstance(turnstile, FakeTurnstileAdapter):
        app.include_router(build_admin_router(turnstile))

    def reload_snapshot() -> dict:
        if not snapshot_file.exists():
            return {"loaded": False, "reason": f"không có {snapshot_file}"}
        accepted = snapshot_store.load(snapshot_file.read_bytes())
        current = snapshot_store.current()
        return {"loaded": accepted, "current_version": current.version if current else None}

    @app.post("/admin/snapshot/reload")
    def snapshot_reload() -> dict:
        """Nạp lại snapshot.json sau khi fake_cmp02 phát version mới (thu hồi, snapshot cũ…)."""
        return reload_snapshot()

    @app.get("/admin/snapshot")
    def snapshot_info() -> dict:
        current = snapshot_store.current()
        if current is None:
            raise HTTPException(404, "Chưa có snapshot hợp lệ")
        return {
            "version": current.version,
            "issued_at": current.issued_at.isoformat(),
            "devices": [{"did": d.did, "status": d.status} for d in current.devices],
            "revoked_employees": sorted(current.revoked_employees),
        }

    reload_snapshot()
    app.state.outbox = outbox
    app.state.replay_guard = replay_guard
    return app
"""Composition root của CMP-01 – nơi DUY NHẤT biết lớp cài đặt cụ thể (DIP).

T1-03: lắp dev_stubs để chạy được Scan API khi adapter thật chưa xong.
T1-05: thay từng stub bằng adapter thật (DeviceBoundQrVerifier, SnapshotStore,
SnapshotFreshnessPolicy, ReplayGuard, SqliteOutbox, TurnstileGatewayFactoryImpl).

Chạy: uvicorn edge.cmp01_kiosk_control.main:app --port 8000
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI

from . import dev_stubs
from .config import EdgeConfig, load_config
from .entry.scan_api import create_app
from .orchestration.kiosk_gate_orchestrator import KioskGateOrchestrator
from .ports import IClock


class SystemClock(IClock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def build_orchestrator(config: EdgeConfig | None = None) -> KioskGateOrchestrator:
    config = config or load_config()
    calls: list[str] = []
    return KioskGateOrchestrator(
        verifiers=[dev_stubs.StubVerifier(calls)],
        snapshot_store=dev_stubs.StubSnapshotStore(),
        freshness_policy=dev_stubs.StubFreshnessPolicy(calls),
        replay_guard=dev_stubs.InMemoryReplayGuard(calls),
        outbox=dev_stubs.InMemoryOutbox(calls),
        turnstile=dev_stubs.StubTurnstile(calls),
        clock=SystemClock(),
        config=config,
    )


def build_app() -> FastAPI:
    return create_app(build_orchestrator())


app = build_app()

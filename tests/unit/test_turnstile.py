"""T4-02 – FakeTurnstileAdapter, factory, adapter khung (ADR-008)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edge.cmp01_kiosk_control.adapters.dry_contact_turnstile_adapter import (
    DryContactTurnstileAdapter,
)
from edge.cmp01_kiosk_control.adapters.fake_turnstile_adapter import (
    FakeTurnstileAdapter, build_admin_router,
)
from edge.cmp01_kiosk_control.adapters.tcp_ip_turnstile_adapter import TcpIpTurnstileAdapter
from edge.cmp01_kiosk_control.adapters.turnstile_gateway_factory import (
    TurnstileGatewayFactoryImpl,
)
from edge.cmp01_kiosk_control.ports import (
    Direction, GateAck, IClock, ITurnstileGateway, TurnstileGatewayFactory,
)

T0 = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc)


class FakeClock(IClock):
    def __init__(self, now: datetime = T0):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def make(mode="OK"):
    clock = FakeClock()
    slept: list[float] = []

    def fake_sleep(sec: float) -> None:
        slept.append(sec)
        clock.advance(sec)

    return FakeTurnstileAdapter(mode=mode, clock=clock, sleep=fake_sleep), clock, slept


def open_gate(adapter, command_id="e1", deadline=T0 + timedelta(seconds=1)):
    return adapter.open_gate("ST-01", "G-01", Direction.IN, command_id, deadline)


# ------------------------------------------------------------ FakeTurnstileAdapter

def test_implements_gateway_port():
    assert isinstance(make()[0], ITurnstileGateway)


def test_ok_mode_returns_opened_without_waiting():
    adapter, _, slept = make("OK")
    assert open_gate(adapter) is GateAck.OPENED
    assert slept == []


def test_error_mode_returns_failed():
    adapter, _, _ = make("ERROR")
    assert open_gate(adapter) is GateAck.FAILED


def test_timeout_mode_waits_until_deadline_then_unconfirmed():
    """Hết deadline → UNCONFIRMED (lệnh có thể đã chạy), KHÔNG phải FAILED."""
    adapter, clock, slept = make("TIMEOUT")
    ack = open_gate(adapter, deadline=T0 + timedelta(milliseconds=800))
    assert ack is GateAck.UNCONFIRMED
    assert slept == [pytest.approx(0.8)]
    assert clock.now() == T0 + timedelta(milliseconds=800)


def test_timeout_with_deadline_already_passed_does_not_sleep():
    adapter, _, slept = make("TIMEOUT")
    assert open_gate(adapter, deadline=T0 - timedelta(seconds=1)) is GateAck.UNCONFIRMED
    assert slept == []


def test_no_blind_retry_one_call_per_open_gate():
    adapter, _, _ = make("TIMEOUT")
    open_gate(adapter, "e1")
    assert adapter.calls == ["e1"]            # adapter không tự gửi lại


def test_calls_record_command_id_in_order():
    adapter, _, _ = make("OK")
    open_gate(adapter, "a")
    open_gate(adapter, "b")
    assert adapter.calls == ["a", "b"]


def test_mode_can_be_changed_at_runtime():
    adapter, _, _ = make("OK")
    adapter.set_mode("ERROR")
    assert adapter.mode == "ERROR" and open_gate(adapter) is GateAck.FAILED


def test_set_mode_is_case_insensitive_and_rejects_unknown():
    adapter, _, _ = make()
    adapter.set_mode("timeout")
    assert adapter.mode == "TIMEOUT"
    with pytest.raises(ValueError):
        adapter.set_mode("BROKEN")


def test_mode_defaults_from_environment(monkeypatch):
    monkeypatch.setenv("TMC_TURNSTILE_MODE", "error")
    assert FakeTurnstileAdapter().mode == "ERROR"
    monkeypatch.delenv("TMC_TURNSTILE_MODE")
    assert FakeTurnstileAdapter().mode == "OK"


def test_unexpected_internal_error_never_raises_and_is_unconfirmed():
    def boom(_):
        raise RuntimeError("sleep hỏng")

    adapter = FakeTurnstileAdapter(mode="TIMEOUT", clock=FakeClock(), sleep=boom)
    assert open_gate(adapter) is GateAck.UNCONFIRMED


# ------------------------------------------------------------ /admin/turnstile-mode

def test_admin_endpoint_get_and_put():
    adapter, _, _ = make("OK")
    app = FastAPI()
    app.include_router(build_admin_router(adapter))
    client = TestClient(app)

    assert client.get("/admin/turnstile-mode").json() == {"mode": "OK"}
    r = client.put("/admin/turnstile-mode", json={"mode": "timeout"})
    assert r.status_code == 200 and r.json() == {"mode": "TIMEOUT"}
    assert adapter.mode == "TIMEOUT"
    assert client.put("/admin/turnstile-mode", json={"mode": "BROKEN"}).status_code == 422


# ------------------------------------------------------------ factory + adapter khung

def test_factory_implements_port_and_picks_adapter_by_type():
    factory = TurnstileGatewayFactoryImpl()
    assert isinstance(factory, TurnstileGatewayFactory)
    assert isinstance(factory.get_gateway("FAKE"), FakeTurnstileAdapter)
    assert isinstance(factory.get_gateway("TCP_IP"), TcpIpTurnstileAdapter)
    assert isinstance(factory.get_gateway("DRY_CONTACT"), DryContactTurnstileAdapter)


def test_factory_returns_same_fake_instance_so_mode_is_shared():
    factory = TurnstileGatewayFactoryImpl()
    assert factory.get_gateway("FAKE") is factory.get_gateway("FAKE")


def test_factory_rejects_unknown_type():
    with pytest.raises(ValueError):
        TurnstileGatewayFactoryImpl().get_gateway("LASER")


@pytest.mark.parametrize("cls", [TcpIpTurnstileAdapter, DryContactTurnstileAdapter])
def test_skeleton_adapters_are_substitutable_lsp(cls):
    """Khung rỗng vẫn giữ hợp đồng: chỉ trả GateAck, không ném ngoại lệ, không báo FAILED sai."""
    gateway = cls()
    assert isinstance(gateway, ITurnstileGateway)
    ack = gateway.open_gate("ST-01", "G-01", Direction.OUT, "e1", T0 + timedelta(seconds=1))
    assert ack is GateAck.UNCONFIRMED

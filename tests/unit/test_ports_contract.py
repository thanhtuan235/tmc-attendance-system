"""Kiểm tra hợp đồng chung T1-02 (tag v0.1-interfaces)."""

from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from edge.cmp01_kiosk_control import ports
from edge.cmp01_kiosk_control.config import EdgeConfig, load_config
from edge.cmp01_kiosk_control.ports import (
    GateAck, QrPayload, ReasonCode, ScanResult, ScanStatus, TxStatus,
)
from packages.contracts import AttendanceEvent

PORTS_DIR = Path(ports.__file__).parent
CONTRACTS_DIR = Path(inspect.getfile(AttendanceEvent)).parent

INTERFACES = [
    ports.IGateScan, ports.IQrVerifier, ports.IReplayGuard, ports.ISnapshotStore,
    ports.ISnapshotFreshnessPolicy, ports.IOutboxRepository, ports.ITurnstileGateway,
    ports.TurnstileGatewayFactory, ports.ICloudEventClient, ports.IClock,
]


@pytest.mark.parametrize("iface", INTERFACES, ids=lambda c: c.__name__)
def test_interface_is_abstract(iface):
    with pytest.raises(TypeError):
        iface()


def test_reason_codes_match_adr_013():
    assert {c.value for c in ReasonCode} == {
        "MALFORMED", "QR_EXPIRED", "FUTURE", "CLOCK_SKEW", "UNKNOWN_DEVICE",
        "DEVICE_REVOKED", "BAD_SIGNATURE", "STALE_SNAPSHOT", "POLICY_DENIED", "REPLAY",
    }


def test_gate_ack_has_exactly_three_results():
    assert {a.value for a in GateAck} == {"OPENED", "UNCONFIRMED", "FAILED"}
    assert {s.value for s in TxStatus} == {"RECEIVED", "OPENED", "UNCONFIRMED", "FAILED"}


def test_canonical_string():
    p = QrPayload(v=1, did="d1", iat=100, seq=5, sig=b"x")
    assert p.canonical() == b"1|d1|100|5"


def test_dto_is_frozen():
    r = ScanResult(status=ScanStatus.ACCEPTED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.status = ScanStatus.REJECTED  # type: ignore[misc]


def test_outbox_record_has_m2_fields():
    names = {f.name for f in dataclasses.fields(ports.OutboxRecord)}
    assert {"event_id", "qr_hash", "station_seq", "command_sent_at", "ack_at", "synced"} <= names


def test_replay_guard_has_check_commit_set_floor():
    assert {"check", "commit", "set_floor"} <= ports.IReplayGuard.__abstractmethods__


def _imported_modules(folder: Path) -> set[str]:
    mods: set[str] = set()
    for py in folder.glob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module)
    return mods


def test_ports_do_not_depend_on_adapters_or_orchestration():
    """R1: ports chỉ phụ thuộc thư viện chuẩn và packages.contracts."""
    for mod in _imported_modules(PORTS_DIR):
        assert "adapters" not in mod and "orchestration" not in mod and "entry" not in mod, mod
        assert mod.split(".")[0] in {"__future__", "abc", "dataclasses", "datetime",
                                     "enum", "typing", "packages"}, mod


def test_contracts_do_not_import_edge():
    """R4: CMP-03 dùng contracts mà không kéo theo code Edge."""
    assert not any(m.startswith("edge") for m in _imported_modules(CONTRACTS_DIR))


def test_attendance_event_roundtrip():
    e = AttendanceEvent(
        event_id="e1", station_id="ST-01", station_seq=1, gate_id="G-01", did="d1", seq=5,
        direction="IN", status="OPENED", credential_type="DEVICE_BOUND",
        snapshot_tier="FRESH", needs_review=False,
        occurred_at=datetime(2026, 11, 20, tzinfo=timezone.utc).isoformat(),
    )
    assert AttendanceEvent.from_dict(e.to_dict()) == e


def test_config_defaults_follow_table_3_5():
    c = EdgeConfig()
    assert (c.qr_window_past_sec, c.qr_window_future_sec) == (30, 5)
    assert (c.snapshot_fresh_min, c.snapshot_stale_warn_min, c.snapshot_entry_max_hours) == (5, 15, 4)
    assert c.clock_max_skew_sec == 5 and c.nfr01_budget_ms == 2000


def test_config_env_override():
    c = load_config({"TMC_SNAPSHOT_ENTRY_MAX_HOURS": "1", "TMC_CLOUD_EVENTS_URL": "http://x/events"})
    assert c.snapshot_entry_max_hours == 1 and c.cloud_events_url == "http://x/events"
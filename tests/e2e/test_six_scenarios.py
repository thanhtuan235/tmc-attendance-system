"""T1-04 – Kiểm thử đầu-cuối 6 kịch bản CMP-01 trên server THẬT.

Mỗi lần chạy, fixture bật CMP-03 mini (Cloud) và CMP-01 (Edge) bằng uvicorn ở tiến trình riêng,
trong một thư mục tạm, với adapter thật lắp tại main.py (không stub). Test gọi HTTP như Kiosk,
tắt/bật Cloud (KB4) và tắt cứng rồi khởi động lại Edge (KB5) để kiểm Recovery.

Thứ tự test có ý nghĩa (dùng chung một hệ thống đang chạy) – KB6 luôn chạy cuối.
Chạy riêng: pytest -q tests/e2e/test_six_scenarios.py
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from tools import fake_mobile

ROOT = Path(__file__).resolve().parents[2]
DEV_OK = "e2e_device_ok"
DEV_REVOKED = "e2e_device_revoked"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class System:
    """Điều khiển Cloud + Edge thật cho kịch bản e2e."""

    def __init__(self, workdir: Path) -> None:
        self.dir = workdir
        self.edge_port, self.cloud_port = _free_port(), _free_port()
        self.edge = f"http://127.0.0.1:{self.edge_port}"
        self.cloud = f"http://127.0.0.1:{self.cloud_port}"
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "TMC_RUNTIME_DIR": str(workdir / "runtime-files"),
            "TMC_SNAPSHOT_FILE": str(workdir / "snapshot.json"),
            "TMC_CLOUD_EVENTS_URL": f"{self.cloud}/events",
            "TMC_SYNC_INTERVAL_SEC": "1",
        }
        self.edge_proc: subprocess.Popen | None = None
        self.cloud_proc: subprocess.Popen | None = None
        self.http = httpx.Client(timeout=5)
        self._snapshot_version = 0

    # ---------------------------------------------------------------- tiến trình

    def _spawn(self, target: str, port: int, log: str, *extra: str) -> subprocess.Popen:
        out = open(self.dir / log, "ab")
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", target, "--port", str(port), *extra],
            cwd=self.dir, env=self.env, stdout=out, stderr=subprocess.STDOUT,
        )

    def _wait_up(self, url: str, proc: subprocess.Popen, log: str) -> None:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"Server thoát sớm:\n{(self.dir / log).read_text(errors='replace')}")
            try:
                self.http.get(url)
                return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError(f"Không bật được {url}")

    def start_cloud(self) -> None:
        self.cloud_proc = self._spawn("cloud.cmp03_attendance_mini.main:app", self.cloud_port, "cloud.log")
        self._wait_up(f"{self.cloud}/events/count", self.cloud_proc, "cloud.log")

    def start_edge(self) -> None:
        self.edge_proc = self._spawn(
            "edge.cmp01_kiosk_control.main:build_app", self.edge_port, "edge.log", "--factory"
        )
        self._wait_up(f"{self.edge}/health", self.edge_proc, "edge.log")

    @staticmethod
    def _stop(proc: subprocess.Popen | None, *, crash: bool = False) -> None:
        if proc is None or proc.poll() is not None:
            return
        if crash or os.name == "nt":
            proc.kill()
        else:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def stop_cloud(self) -> None:
        self._stop(self.cloud_proc)

    def crash_edge(self) -> None:
        self._stop(self.edge_proc, crash=True)

    def shutdown(self) -> None:
        self._stop(self.edge_proc)
        self._stop(self.cloud_proc)
        self.http.close()

    # ---------------------------------------------------------------- công cụ giả lập

    def tool(self, module: str, *args: str) -> str:
        res = subprocess.run(
            [sys.executable, "-m", module, *args], cwd=self.dir, env=self.env,
            capture_output=True, text=True, check=True,
        )
        return res.stdout.strip()

    def qr(self, did: str = DEV_OK, *extra: str) -> str:
        return self.tool("tools.fake_mobile", "--did", did, *extra)

    def publish_snapshot(self, *, revoke: list[str] = (), issued_at: str | None = None, reload: bool = True) -> None:
        self._snapshot_version += 1
        args = ["--device", DEV_OK, "emp_ok", "--device", DEV_REVOKED, "emp_revoked",
                "--version", str(self._snapshot_version)]
        for did in revoke:
            args += ["--revoke-device", did]
        if issued_at:
            args += ["--issued-at", issued_at]
        self.tool("tools.fake_cmp02", *args)
        if reload:
            r = self.http.post(f"{self.edge}/admin/snapshot/reload").json()
            assert r["loaded"] and r["current_version"] == self._snapshot_version, r

    # ---------------------------------------------------------------- HTTP

    def scan(self, qr: str, direction: str = "IN", station: str = "BD-01") -> dict:
        body = {"station_id": station, "gate_id": "G1", "direction": direction, "qr": qr}
        resp = self.http.post(f"{self.edge}/scan", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def count(self) -> int:
        return self.http.get(f"{self.cloud}/events/count").json()["count"]

    def status(self) -> dict:
        return self.http.get(f"{self.edge}/status").json()

    def gate(self, mode: str) -> None:
        self.http.put(f"{self.edge}/admin/turnstile-mode", json={"mode": mode}).raise_for_status()

    def wait_until(self, cond, timeout: float = 15.0, what: str = "") -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.2)
        raise AssertionError(f"Hết thời gian chờ: {what}")


@pytest.fixture(scope="module")
def system(tmp_path_factory):
    work = tmp_path_factory.mktemp("e2e")
    sysm = System(work)
    try:
        for did in (DEV_OK, DEV_REVOKED):
            sysm.qr(did, "--export-public-key")
        sysm.publish_snapshot(reload=False)      # v1 trước khi Edge khởi động
        sysm.start_cloud()
        sysm.start_edge()
        yield sysm
    finally:
        sysm.shutdown()
        _cleanup_devices()


def _cleanup_devices() -> None:
    for did in (DEV_OK, DEV_REVOKED):            # chỉ dọn khóa + trạng thái của 2 thiết bị e2e
        fake_mobile.private_key_path(did).unlink(missing_ok=True)
        fake_mobile.state_path(did).unlink(missing_ok=True)


def ok(r: dict) -> tuple:
    return r["status"], r["tx_status"]


def rejected(r: dict) -> tuple:
    return r["status"], r["reason_code"]


# ═══════════════════════════════ KB1 ═══════════════════════════════

def test_kb1_valid_scan_opens_gate_and_syncs_to_cloud(system: System):
    r = system.scan(system.qr())
    assert ok(r) == ("ACCEPTED", "OPENED")
    system.wait_until(lambda: system.count() == 1, what="CMP-03 nhận bản ghi KB1")


# ═══════════════════════════════ KB2 ═══════════════════════════════

def test_kb2_replayed_qr_at_another_gate_is_rejected(system: System):
    q = system.qr()
    assert ok(system.scan(q)) == ("ACCEPTED", "OPENED")
    assert rejected(system.scan(q, station="BD-02")) == ("REJECTED", "REPLAY")


def test_kb2_same_qr_at_same_gate_returns_cached_result(system: System):
    q = system.qr()
    first = system.scan(q)
    again = system.scan(q)
    assert again["event_id"] == first["event_id"] and again["replayed_result"] is True


def test_kb2_forged_signature_is_rejected(system: System):
    r = system.scan(system.qr(DEV_OK, "--tamper-sig"))
    assert rejected(r) == ("REJECTED", "BAD_SIGNATURE")


# ═══════════════════════════════ KB3 ═══════════════════════════════

def test_kb3_revoked_device_is_rejected_at_edge(system: System):
    system.publish_snapshot(revoke=[DEV_REVOKED])
    assert rejected(system.scan(system.qr(DEV_REVOKED))) == ("REJECTED", "DEVICE_REVOKED")
    assert ok(system.scan(system.qr(DEV_OK))) == ("ACCEPTED", "OPENED")


# ═══════════════════════════════ KB4 ═══════════════════════════════

def test_kb4_cloud_outage_keeps_gate_open_and_syncs_without_loss(system: System):
    system.wait_until(lambda: system.status()["pending_sync"] == 0, what="outbox trống trước KB4")
    before = system.count()

    system.stop_cloud()
    for _ in range(3):
        assert ok(system.scan(system.qr())) == ("ACCEPTED", "OPENED")
    system.wait_until(lambda: system.status()["cloud_consecutive_failures"] >= 1, what="Sync Agent ghi nhận lỗi")
    assert system.status()["pending_sync"] == 3

    system.start_cloud()
    system.wait_until(lambda: system.status()["pending_sync"] == 0, timeout=30, what="đồng bộ lại")
    assert system.count() == before + 3            # đủ, không mất, không trùng


# ═══════════════════════════════ KB5 ═══════════════════════════════

def test_kb5_turnstile_timeout_is_unconfirmed(system: System):
    system.gate("TIMEOUT")
    try:
        assert ok(system.scan(system.qr())) == ("ACCEPTED", "UNCONFIRMED")
    finally:
        system.gate("OK")


def test_kb5_edge_crash_restart_recovers_and_blocks_old_qr(system: System):
    old_qr = system.qr()
    time.sleep(1.1)                                 # sàn replay tính theo giây
    system.crash_edge()
    system.start_edge()

    assert system.status()["recovery"]["replay_floor"]
    assert rejected(system.scan(old_qr)) == ("REJECTED", "REPLAY")
    assert ok(system.scan(system.qr())) == ("ACCEPTED", "OPENED")
    system.wait_until(lambda: system.status()["pending_sync"] == 0, what="đồng bộ sau khởi động lại")


# ═══════════════════════════════ KB6 (luôn cuối) ═══════════════════════════════

def test_kb6_stale_snapshot_blocks_entry_but_allows_exit_with_review(system: System):
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    system.publish_snapshot(issued_at=old)

    assert rejected(system.scan(system.qr(), "IN")) == ("REJECTED", "STALE_SNAPSHOT")
    r = system.scan(system.qr(), "OUT")
    assert ok(r) == ("ACCEPTED", "OPENED") and r["needs_review"] is True

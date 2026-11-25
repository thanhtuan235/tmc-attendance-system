from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from fastapi.testclient import TestClient

from cloud.cmp03_attendance_mini.main import create_app
from edge.cmp01_kiosk_control.adapters.snapshot_store import SnapshotStore
from edge.cmp01_kiosk_control.config import EdgeConfig
from edge.cmp01_kiosk_control.ports import Direction, SnapshotTier
from edge.cmp01_kiosk_control.snapshot_freshness_policy import SnapshotFreshnessPolicy
from packages.contracts import AttendanceEvent
from tools import fake_cmp02, fake_mobile


def test_publisher_store_and_policy_agree_after_competing_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(fake_mobile, "DEVICES_DIR", tmp_path / "devices")
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    pin = tmp_path / "root_pub.pem"
    signing_key = tmp_path / "root.pem"
    db = tmp_path / "edge_cache.db"

    def publish(version, issued_at, signer=signing_key, root=pin):
        output = tmp_path / "snapshot.json"
        assert fake_cmp02.main([
            "--device", "device_001", "employee_001", "--version", str(version),
            "--issued-at", issued_at.isoformat(), "--signing-key", str(signer),
            "--root-public-key", str(root), "--output", str(output),
        ]) == 0
        return output.read_bytes()

    snapshots = [publish(1, now), publish(2, now - timedelta(hours=4, seconds=1))]
    stores = [SnapshotStore(db, pin), SnapshotStore(db, pin)]
    ready = Barrier(2, timeout=5)

    def load(index):
        ready.wait()
        return stores[index].load(snapshots[index])

    with ThreadPoolExecutor(max_workers=2) as workers:
        _, newest_accepted = workers.map(load, range(2))
    assert newest_accepted

    other_root = tmp_path / "other_pub.pem"
    wrong_signature = publish(3, now, tmp_path / "other.pem", other_root)
    reopened = SnapshotStore(db, pin)
    assert not reopened.load(wrong_signature)
    snapshot = reopened.current()
    assert snapshot is not None and snapshot.version == 2
    assert snapshot.find("device_001").public_key_pem == (
        fake_mobile.export_public_key("device_001", as_pem=True).decode("ascii")
    )
    policy = SnapshotFreshnessPolicy(EdgeConfig())
    assert policy.evaluate(snapshot, Direction.IN, now).tier is SnapshotTier.EXPIRED
    assert policy.evaluate(snapshot, Direction.OUT, now).needs_review


def test_cmp03_deduplicates_by_event_id_within_and_across_batches(tmp_path):
    event = AttendanceEvent(
        event_id="e1", station_id="ST-01", station_seq=1, gate_id="G-01",
        did="device_001", seq=7, direction="OUT", status="OPENED",
        credential_type="DEVICE_BOUND", snapshot_tier="EXPIRED", needs_review=True,
        occurred_at="2026-11-20T12:00:00Z",
    ).to_dict()
    second = dict(event, event_id="e2", station_id="ST-02")
    third = dict(event, event_id="e3", station_seq=2)
    with TestClient(create_app(tmp_path / "cloud.db", batch_size=3)) as client:
        first = client.post("/events", json={"events": [event, event]})
        assert first.status_code == 200
        assert first.json() == {"accepted": 1, "duplicates": 1}
        mixed = client.post("/events", json={"events": [event, second, second]})
        assert mixed.status_code == 200
        assert mixed.json() == {"accepted": 1, "duplicates": 2}
        for invalid_batch in (
            [third, dict(event, occurred_at="2026-11-20T12:00:00")],
            [third, dict(event, schema_version=2)],
            [third, event, second, event],
        ):
            invalid = client.post("/events", json={"events": invalid_batch})
            assert invalid.status_code == 400
            assert invalid.json().keys() == {"error"}
        assert client.get("/events/count").json() == {"count": 2}

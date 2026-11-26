from fastapi.testclient import TestClient

from cloud.cmp03_attendance_mini.main import create_app
from packages.contracts import AttendanceEvent


def test_cmp03_validates_batch_and_counts_retries(tmp_path):
    db_path = tmp_path / "cloud.db"
    app = create_app(db_path)
    event = AttendanceEvent(
        event_id="event-1",
        station_id="ST-01",
        station_seq=1,
        gate_id="G-01",
        did="device-1",
        seq=2,
        direction="IN",
        status="OPENED",
        credential_type="DEVICE_BOUND",
        snapshot_tier="FRESH",
        needs_review=False,
        occurred_at="2026-11-20T00:15:03Z",
    ).to_dict()

    assert not db_path.exists()
    with TestClient(app) as client:
        batch = {"events": [event]}
        first = client.post("/events", json=batch)
        assert first.status_code == 200
        assert first.json() == {"accepted": 1, "duplicates": 0}
        retry = client.post("/events", json=batch)
        assert retry.status_code == 200
        assert retry.json() == {"accepted": 0, "duplicates": 1}

        invalid = client.post(
            "/events",
            json={
                "events": [
                    dict(event, event_id="event-2"),
                    dict(event, event_id="event-3", status="RECEIVED"),
                ],
            },
        )
        assert invalid.status_code == 400
        assert invalid.json().keys() == {"error"}
        assert client.get("/events/count").json() == {"count": 1}

    with TestClient(create_app(db_path)) as restarted:
        assert restarted.get("/events/count").json() == {"count": 1}

from datetime import datetime, timedelta, timezone

import pytest

from edge.cmp01_kiosk_control.config import EdgeConfig
from edge.cmp01_kiosk_control.ports import (
    DeviceKeySnapshot,
    Direction,
    FreshnessDecision,
    ReasonCode,
    SnapshotTier,
)
from edge.cmp01_kiosk_control.snapshot_freshness_policy import SnapshotFreshnessPolicy


NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
FRESH = FreshnessDecision(SnapshotTier.FRESH, allow=True)
STALE = FreshnessDecision(SnapshotTier.STALE, allow=True, stale_snapshot=True)
STALE_WARN = FreshnessDecision(SnapshotTier.STALE, allow=True, stale_snapshot=True, warn=True)


def snapshot_at_age(seconds: int) -> DeviceKeySnapshot:
    return DeviceKeySnapshot(1, NOW - timedelta(seconds=seconds), "demo", (), frozenset(), b"")


@pytest.mark.parametrize("direction", list(Direction))
@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [(300, FRESH), (301, STALE), (900, STALE), (901, STALE_WARN), (14_400, STALE_WARN)],
)
def test_snapshot_age_boundaries(direction, age_seconds, expected):
    policy = SnapshotFreshnessPolicy(EdgeConfig())
    assert policy.evaluate(snapshot_at_age(age_seconds), direction, NOW) == expected


@pytest.mark.parametrize("snapshot", [None, snapshot_at_age(14_401)])
def test_expired_or_missing_snapshot_depends_on_direction(snapshot):
    policy = SnapshotFreshnessPolicy(EdgeConfig())
    assert policy.evaluate(snapshot, Direction.IN, NOW) == FreshnessDecision(
        SnapshotTier.EXPIRED, allow=False, reason_code=ReasonCode.STALE_SNAPSHOT
    )
    assert policy.evaluate(snapshot, Direction.OUT, NOW) == FreshnessDecision(
        SnapshotTier.EXPIRED, allow=True, needs_review=True
    )


def test_thresholds_use_edge_config():
    policy = SnapshotFreshnessPolicy(
        EdgeConfig(snapshot_fresh_min=1, snapshot_stale_warn_min=2, snapshot_entry_max_hours=1)
    )
    assert policy.evaluate(snapshot_at_age(61), Direction.IN, NOW) == STALE
    assert policy.evaluate(snapshot_at_age(121), Direction.IN, NOW) == STALE_WARN
    assert policy.evaluate(snapshot_at_age(3601), Direction.IN, NOW).tier == SnapshotTier.EXPIRED

"""Offline scan policy based on the age of the current device snapshot."""

from datetime import datetime, timedelta

from .config import EdgeConfig
from .ports import (
    DeviceKeySnapshot,
    Direction,
    FreshnessDecision,
    ISnapshotFreshnessPolicy,
    ReasonCode,
    SnapshotTier,
)


class SnapshotFreshnessPolicy(ISnapshotFreshnessPolicy):
    def __init__(self, config: EdgeConfig) -> None:
        self._fresh_age = timedelta(minutes=config.snapshot_fresh_min)
        self._warn_age = timedelta(minutes=config.snapshot_stale_warn_min)
        self._entry_max_age = timedelta(hours=config.snapshot_entry_max_hours)

    def evaluate(
        self,
        snapshot: DeviceKeySnapshot | None,
        direction: Direction,
        now: datetime,
    ) -> FreshnessDecision:
        age = now - snapshot.issued_at if snapshot is not None else None
        if age is None or age > self._entry_max_age:
            if direction == Direction.OUT:
                return FreshnessDecision(SnapshotTier.EXPIRED, allow=True, needs_review=True)
            return FreshnessDecision(
                SnapshotTier.EXPIRED, allow=False, reason_code=ReasonCode.STALE_SNAPSHOT
            )

        if age <= self._fresh_age:
            return FreshnessDecision(SnapshotTier.FRESH, allow=True)
        return FreshnessDecision(
            SnapshotTier.STALE, allow=True, stale_snapshot=True, warn=age > self._warn_age
        )

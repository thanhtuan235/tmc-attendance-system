"""In-memory replay protection and replay-floor policy."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from ..ports.dto import ReasonCode
from ..ports.replay_guard import IReplayGuard


class ReplayFloorPolicy:
    """Manage a monotonically increasing replay floor."""

    def __init__(self) -> None:
        self._floor_epoch: Optional[int] = None
        self._lock = threading.Lock()

    @property
    def replay_floor(self) -> Optional[int]:
        """Return the current replay floor as a Unix timestamp."""
        with self._lock:
            return self._floor_epoch

    @property
    def replay_floor_datetime(self) -> Optional[datetime]:
        """Return the current replay floor as an aware UTC datetime."""
        with self._lock:
            if self._floor_epoch is None:
                return None
            return datetime.fromtimestamp(self._floor_epoch, tz=timezone.utc)

    @property
    def floor(self) -> Optional[int]:
        """Compatibility alias for the current replay-floor epoch."""
        return self.replay_floor

    def set_floor(self, timestamp: datetime | int) -> None:
        """Set the floor, never lowering an existing higher floor."""
        epoch = self._datetime_to_epoch(timestamp)
        with self._lock:
            if self._floor_epoch is None or epoch > self._floor_epoch:
                self._floor_epoch = epoch

    def check(self, iat: int) -> Optional[ReasonCode]:
        """Return REPLAY when iat is below the configured replay floor."""
        if not isinstance(iat, int) or isinstance(iat, bool) or iat < 0:
            return ReasonCode.REPLAY
        with self._lock:
            if self._floor_epoch is not None and iat < self._floor_epoch:
                return ReasonCode.REPLAY
        return None

    @staticmethod
    def _datetime_to_epoch(timestamp: datetime | int) -> int:
        if isinstance(timestamp, int) and not isinstance(timestamp, bool):
            if timestamp < 0:
                raise ValueError("Replay floor timestamp must be non-negative")
            return timestamp
        if not isinstance(timestamp, datetime):
            raise ValueError("Replay floor must be a datetime or Unix timestamp")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        return int(timestamp.timestamp())


class ReplayGuard(IReplayGuard):
    """Thread-safe per-device sequence watermark and replay-floor guard."""

    def __init__(self, replay_floor_policy: Optional[ReplayFloorPolicy] = None) -> None:
        self.watermarks: dict[str, int] = {}
        self.replay_floor_policy = replay_floor_policy or ReplayFloorPolicy()
        self._lock = threading.RLock()

    @property
    def replay_floor(self) -> Optional[int]:
        return self.replay_floor_policy.replay_floor

    def get_watermark(self, did: str) -> Optional[int]:
        """Return the latest accepted sequence for did, if one exists."""
        with self._lock:
            return self.watermarks.get(did)

    def check(self, did: str, seq: int, iat: int) -> Optional[ReasonCode]:
        """Check replay rules without changing the watermark."""
        if not isinstance(did, str) or not did:
            return ReasonCode.REPLAY
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return ReasonCode.REPLAY
        if not isinstance(iat, int) or isinstance(iat, bool) or iat < 0:
            return ReasonCode.REPLAY

        with self._lock:
            floor_result = self.replay_floor_policy.check(iat)
            if floor_result is not None:
                return floor_result
            watermark = self.watermarks.get(did)
            if watermark is not None and seq <= watermark:
                return ReasonCode.REPLAY
        return None

    def commit(self, did: str, seq: int) -> bool:
        """Atomically commit a sequence as accepted and advance the watermark."""
        if not isinstance(did, str) or not did:
            return False
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return False

        with self._lock:
            watermark = self.watermarks.get(did)
            if watermark is not None and seq <= watermark:
                return False
            self.watermarks[did] = seq
            return True

    def set_floor(self, ts: datetime | int) -> None:
        """Set the replay floor through the centralized floor policy."""
        self.replay_floor_policy.set_floor(ts)

    def accept(self, did: str, seq: int, iat: int) -> Optional[ReasonCode]:
        """Atomically check and commit a verified QR."""
        with self._lock:
            replay_reason = self.check(did, seq, iat)
            if replay_reason is not None:
                return replay_reason
            if not self.commit(did, seq):
                return ReasonCode.REPLAY
        return None

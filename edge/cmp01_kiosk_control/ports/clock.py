"""IClock – đồng hồ Edge (R7; port EdgeClock của Δ-Q2.14)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class IClock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Thời điểm hiện tại, datetime có tzinfo UTC. Test tiêm FakeClock."""
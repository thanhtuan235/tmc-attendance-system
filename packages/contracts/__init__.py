"""Published Language giữa CMP-01 (Edge) và CMP-03 (Cloud).

Theo R4, CMP-03 không import ports của Edge; hai bên chỉ chia sẻ gói này.
"""

from .attendance_event import AttendanceEvent

__all__ = ["AttendanceEvent"]
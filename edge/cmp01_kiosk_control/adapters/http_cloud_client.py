"""HttpCloudClient – cài ICloudEventClient bằng httpx (T4-04, thành phần 9).

Gửi lô sự kiện tới POST /events của CMP-03 (docs/contracts/cmp03_events.md).
Hợp đồng port: không ném ngoại lệ; True khi CMP-03 trả 200 (kể cả bản ghi trùng).
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

import httpx

from packages.contracts import AttendanceEvent

from ..ports import ICloudEventClient

log = logging.getLogger("cmp01.sync")


class HttpCloudClient(ICloudEventClient):
    def __init__(
        self,
        events_url: str,
        *,
        timeout_sec: float = 3.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._url = events_url
        self._client = client or httpx.Client(timeout=timeout_sec)

    def send_batch(self, events: Sequence[AttendanceEvent]) -> bool:
        if not events:
            return True
        try:
            resp = self._client.post(self._url, json={"events": [e.to_dict() for e in events]})
        except httpx.HTTPError as exc:
            log.warning("CMP-03 không kết nối được (%s): giữ %d bản ghi để thử lại", exc, len(events))
            return False
        if resp.status_code == 200:
            return True
        if resp.status_code == 400:
            # Lỗi định dạng: thử lại cũng không qua; ghi log để điều tra.
            log.error("CMP-03 từ chối lô %d bản ghi (400): %s", len(events), resp.text)
        else:
            log.warning("CMP-03 trả %s: giữ lô để thử lại", resp.status_code)
        return False

    def close(self) -> None:
        self._client.close()

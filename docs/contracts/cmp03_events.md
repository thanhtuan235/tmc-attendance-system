# Hợp đồng POST /events (CMP-01 → CMP-03 mini)

Phiên bản: v0.1-interfaces (T1-02). Kiểu dữ liệu: `packages/contracts/attendance_event.py`.

## Request

`POST http://<cmp03>:8001/events` — `Content-Type: application/json`

```json
{
  "events": [
    {
      "event_id": "9f2c…",
      "station_id": "ST-01",
      "station_seq": 42,
      "gate_id": "G-01",
      "did": "dev-001",
      "seq": 17,
      "direction": "IN",
      "status": "OPENED",
      "credential_type": "DEVICE_BOUND",
      "snapshot_tier": "FRESH",
      "needs_review": false,
      "occurred_at": "2026-11-20T00:15:03Z",
      "ack_at": "2026-11-20T00:15:04Z",
      "reason_code": null,
      "schema_version": 1
    }
  ]
}
```

- Chỉ gửi bản ghi đã có **trạng thái cuối** (OPENED, UNCONFIRMED, FAILED).
- Lô tối đa `sync_batch_size` (mặc định 100) bản ghi.

## Response

| Mã | Body | Ý nghĩa với OutboxRelay |
|---|---|---|
| 200 | `{"accepted": n, "duplicates": m}` | `mark_synced` toàn bộ lô |
| 400 | `{"error": "..."}` | Lỗi định dạng: ghi log, không thử lại lô đó |
| 5xx / lỗi mạng | — | Giữ nguyên, backoff có jitter, thử lại |

## Quy tắc

1. **Idempotent theo `event_id`**: bản ghi đã có thì bỏ qua, đếm vào `duplicates`, vẫn trả 200.
2. Giao nhận **at-least-once**; không giả định exactly-once.
3. Chỉ mục **không duy nhất** `(did, seq)` để phát hiện dùng lại QR (ADR-014).
4. `GET /events/count` → `{"count": n}` để demo.
5. Tùy chọn: `GET /stations/{station_id}/gaps` → danh sách `station_seq` bị thiếu.

Giới hạn Task 2: CMP-03 mini chỉ nhận trạng thái cuối, không nhận từng bản ghi chuyển trạng thái.
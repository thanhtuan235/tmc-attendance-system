# Kiểm thử tích hợp T3

Các ca dưới đây theo mục 3.2 của báo cáo M2. Chạy với Python 3.11 trở lên sau khi cài `pip install -e ".[dev]"`.

```powershell
python -m pytest -q tests/e2e/test_t3_snapshot_scenarios.py
```

Bộ test cần Scan API và orchestrator của T1-03, verifier và replay guard của T2, SQLite outbox của T4-01 và cổng giả của T4-02. Pytest báo skip nếu checkout chưa có một trong các thành phần này. Các test dùng khóa demo, database tạm và đồng hồ cố định.

## S3 — Xác minh tại Edge

1. Phát snapshot mới với public key của thiết bị demo. Quét QR mới chiều vào để kiểm tra cổng mở.
2. Nạp snapshot version cao hơn, đã 16 phút tuổi. Quét QR mới để kiểm tra cổng mở và phản hồi có `stale_snapshot`. Kiểm tra cờ `warn` trên kết quả freshness policy.
3. Nạp snapshot version cao hơn, đã quá 4 giờ. Hai QR mới chiều vào và ra phải có `seq` khác nhau. Chiều vào trả `STALE_SNAPSHOT` và không có lệnh mở. Chiều ra có `needs_review` trên kết quả scan và bản ghi outbox.
4. Dùng bản ghi OUT để gửi `POST /events` hai lần. CMP-03 nhận 1 bản mới trong lần đầu, đếm 1 bản trùng trong lần sau và `GET /events/count` trả 1.

Ca tự động chuyển bản ghi outbox sang HTTP trong test. Khi OutboxRelay của T4 sẵn sàng, kiểm tra lại với relay chạy thật, giữ `needs_review` và `snapshot_tier` trong payload và bảo đảm bản ghi được đánh dấu đã đồng bộ sau HTTP 200.

## S6 — Bảo vệ vận hành

Ca tự động mô phỏng verifier trả `CLOCK_SKEW` và kiểm tra phản hồi scan, bản ghi log `cmp01.audit` và số lệnh mở cổng bằng 0.

Khi nguồn đo lệch đồng hồ của Edge sẵn sàng, dùng nguồn tham chiếu trong môi trường test. So sánh số đo đúng ngưỡng cấu hình và số đo vượt ngưỡng. Lượt vượt ngưỡng phải trả `CLOCK_SKEW`, phát cảnh báo và có audit bền theo thiết kế M2. Phép đo thực, cảnh báo và lưu audit bền cần nghiệm thu ở luồng Edge đã lắp đầy đủ.

## Chạy công cụ demo

```powershell
python -m tools.fake_mobile --did device_001
python -m tools.fake_cmp02 --device device_001 employee_001 --version 1
python -m uvicorn cloud.cmp03_attendance_mini.main:app --port 8001
```

CLI ghi `snapshot.json` và `runtime-files/pinned_root_pub.pem`. Ở composition root của Edge, tạo `SnapshotStore` với đường dẫn database và khóa công khai ghim, gọi `store.load` với nội dung file snapshot, rồi truyền store và `SnapshotFreshnessPolicy` vào orchestrator. Đặt `--issued-at` theo UTC và tăng `--version` để thay snapshot trong các lượt thử.

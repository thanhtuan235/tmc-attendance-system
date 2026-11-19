# tmc-attendance-system

Task 2 – Hiện thực module **CMP-01 Kiosk Control** (Edge) và **CMP-03 mini** (Cloud) cho hệ thống TMC QR Attendance & Access Control.

> README đầy đủ (kiến trúc, cách chạy, truy vết Δ/ADR) sẽ được viết ở T1-06.

## Cấu trúc thư mục (theo Implementation view P8-04)

```
edge/cmp01_kiosk_control/
  entry/          # Scan API (FastAPI)
  orchestration/  # KioskGateOrchestrator, Recovery, Sync Agent
  ports/          # Interface + DTO dùng chung (T1-02)
  adapters/       # Verifier, ReplayGuard, Snapshot, Outbox, Turnstile, Cloud client
cloud/cmp03_attendance_mini/  # Nhận sự kiện idempotent
tools/            # fake_mobile.py, fake_cmp02.py
tests/unit/  tests/e2e/
```

## Cài đặt

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

## Quy trình Git

`main` (bản nộp) ← `dev` (tích hợp) ← `feature/<MãCV>-<mô-tả>`. Không push thẳng vào `main`/`dev`; mọi thay đổi qua Pull Request vào `dev`.

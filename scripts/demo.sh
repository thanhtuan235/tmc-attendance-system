#!/usr/bin/env bash
# T2-05 – Demo 6 kịch bản CMP-01 Kiosk Control (Linux/macOS).
# Script tự bật/tắt/khởi động lại Cloud (CMP-03 mini) và Edge (CMP-01), tự kiểm tra kết quả.
#
#   bash scripts/demo.sh            # dừng chờ Enter giữa các kịch bản (thuyết trình)
#   bash scripts/demo.sh --no-pause # chạy liền một mạch (kiểm thử nhanh, CI)
#
# Yêu cầu: chạy ở thư mục gốc repo, đã `pip install -e .[dev]`, cổng 8000/8001 đang trống.

set -uo pipefail
cd "$(dirname "$0")/.."

PAUSE=1; [[ "${1:-}" == "--no-pause" ]] && PAUSE=0
PY=${PYTHON:-python3}
EDGE=http://127.0.0.1:8000
CLOUD=http://127.0.0.1:8001
LOGS=demo-logs
export TMC_SYNC_INTERVAL_SEC=1          # đồng bộ mỗi 1 giây cho demo nhanh
export TMC_CLOUD_EVENTS_URL=$CLOUD/events

PASS=0; FAIL=0; RESULTS=()
EDGE_PID=""; CLOUD_PID=""

c_title=$'\e[1;36m'; c_ok=$'\e[32m'; c_bad=$'\e[31m'; c_dim=$'\e[2m'; c_off=$'\e[0m'

title() { echo; echo "${c_title}══ $* ══${c_off}"; }
info()  { echo "${c_dim}  $*${c_off}"; }
pause() { (( PAUSE )) && read -rp "  ⏎ Enter để tiếp tục..." _; true; }

check() {  # check "<mô tả>" "<thực tế>" "<mong đợi>"
  if [[ "$2" == "$3" ]]; then echo "  ${c_ok}✔ $1: $2${c_off}"; PASS=$((PASS+1))
  else echo "  ${c_bad}✘ $1: nhận '$2', mong đợi '$3'${c_off}"; FAIL=$((FAIL+1)); fi
}

json() { $PY -c "import json,sys;d=json.load(sys.stdin);print(d$1)"; }
qr()   { $PY -m tools.fake_mobile "$@"; }
snapshot() { $PY -m tools.fake_cmp02 "$@" >/dev/null && curl -s -XPOST $EDGE/admin/snapshot/reload >/dev/null; }
gate() { curl -s -XPUT $EDGE/admin/turnstile-mode -H 'content-type: application/json' -d "{\"mode\":\"$1\"}" >/dev/null; }
count()   { curl -s $CLOUD/events/count | json '["count"]'; }
pending() { curl -s $EDGE/status | json '["pending_sync"]'; }

scan() {  # scan <qr> [IN|OUT] [station] → in "status reason tx_status needs_review"
  local body
  body=$(printf '{"station_id":"%s","gate_id":"G1","direction":"%s","qr":"%s"}' "${3:-BD-01}" "${2:-IN}" "$1")
  curl -s $EDGE/scan -H 'content-type: application/json' -d "$body" | tee -a $LOGS/scan.jsonl |
    $PY -c 'import json,sys;d=json.load(sys.stdin);print(d["status"],d["reason_code"] or "-",d["tx_status"] or "-",d["needs_review"])'
}

wait_up() { for _ in $(seq 50); do curl -s -o /dev/null "$1" && return 0; sleep 0.2; done; echo "Không bật được $1"; exit 1; }
start_cloud() { $PY -m uvicorn cloud.cmp03_attendance_mini.main:app --port 8001 >>$LOGS/cloud.log 2>&1 & CLOUD_PID=$!; wait_up $CLOUD/events/count; }
start_edge()  { $PY -m uvicorn edge.cmp01_kiosk_control.main:build_app --factory --port 8000 >>$LOGS/edge.log 2>&1 & EDGE_PID=$!; wait_up $EDGE/health; }
stop() { [[ -n "$1" ]] && kill "$1" 2>/dev/null && wait "$1" 2>/dev/null; true; }
cleanup() { stop "$EDGE_PID"; stop "$CLOUD_PID"; }
trap cleanup EXIT

record() { RESULTS+=("$1|$([[ $FAIL == "$2" ]] && echo ĐẠT || echo LỖI)"); }

# ───────────────────────────── Chuẩn bị ─────────────────────────────
title "Chuẩn bị: xóa dữ liệu cũ, cấp khóa thiết bị, phát snapshot v1, bật Cloud + Edge"
rm -rf runtime-files snapshot.json cmp03_events.db tools/devices $LOGS; mkdir -p $LOGS
qr --did device_001 --export-public-key >/dev/null
qr --did device_002 --export-public-key >/dev/null
$PY -m tools.fake_cmp02 --device device_001 emp_001 --device device_002 emp_002 --version 1 >/dev/null
start_cloud; start_edge
info "Cloud: $CLOUD/docs   Edge: $EDGE/docs   Log: $LOGS/"
pause

# ───────────────────────────── KB1 ─────────────────────────────
f=$FAIL; title "KB1 – Quét QR hợp lệ → mở cổng, ghi nhận, đồng bộ lên Cloud"
Q1=$(qr --did device_001)
read -r s r t _ <<<"$(scan "$Q1")"
check "Kết quả quét" "$s/$t" "ACCEPTED/OPENED"
sleep 2; check "Số bản ghi trên Cloud" "$(count)" "1"
record "KB1 Quét hợp lệ" $f; pause

# ───────────────────────────── KB2 ─────────────────────────────
f=$FAIL; title "KB2 – Chống dùng lại QR (chụp màn hình đem quét ở cổng khác) và QR giả"
read -r s r _ <<<"$(scan "$Q1" IN BD-02)";            check "QR cũ ở cổng BD-02" "$s/$r" "REJECTED/REPLAY"
read -r s r _ <<<"$(scan "$(qr --did device_001 --tamper-sig)")"; check "QR sai chữ ký" "$s/$r" "REJECTED/BAD_SIGNATURE"
record "KB2 Replay + giả mạo" $f; pause

# ───────────────────────────── KB3 ─────────────────────────────
f=$FAIL; title "KB3 – Thu hồi thiết bị device_002 (snapshot v2) → bị từ chối ngay tại Edge"
snapshot --device device_001 emp_001 --device device_002 emp_002 --revoke-device device_002 --version 2
read -r s r _ <<<"$(scan "$(qr --did device_002)")";   check "device_002 (đã thu hồi)" "$s/$r" "REJECTED/DEVICE_REVOKED"
read -r s _ t _ <<<"$(scan "$(qr --did device_001)")"; check "device_001 (bình thường)" "$s/$t" "ACCEPTED/OPENED"
record "KB3 Thu hồi thiết bị" $f; pause

# ───────────────────────────── KB4 ─────────────────────────────
f=$FAIL; title "KB4 – Mất kết nối Cloud: cổng vẫn mở, bản ghi giữ trong outbox, có mạng thì đồng bộ đủ"
sleep 2; N=$(count); info "Cloud đang có $N bản ghi. Tắt Cloud..."
stop "$CLOUD_PID"; CLOUD_PID=""
for i in 1 2 3; do read -r s _ t _ <<<"$(scan "$(qr --did device_001)")"; check "Quét lần $i khi mất Cloud" "$s/$t" "ACCEPTED/OPENED"; done
sleep 2; check "Bản ghi chờ đồng bộ (pending_sync)" "$(pending)" "3"
info "Bật lại Cloud..."; start_cloud
for _ in $(seq 30); do [[ "$(pending)" == 0 ]] && break; sleep 0.5; done
check "pending_sync sau khi có mạng" "$(pending)" "0"
check "Cloud nhận đủ, không trùng" "$(count)" "$((N+3))"
record "KB4 Mất Cloud → đồng bộ lại" $f; pause

# ───────────────────────────── KB5 ─────────────────────────────
f=$FAIL; title "KB5 – Turnstile không phản hồi → UNCONFIRMED; Edge sập và khởi động lại → Recovery"
gate TIMEOUT
read -r s _ t _ <<<"$(scan "$(qr --did device_001)")"; check "Cổng không ACK" "$s/$t" "ACCEPTED/UNCONFIRMED"
gate OK
OLD=$(qr --did device_001); info "Đã sinh QR trước khi Edge sập. Tắt cứng Edge rồi khởi động lại..."
kill -9 "$EDGE_PID" 2>/dev/null; wait "$EDGE_PID" 2>/dev/null; sleep 1; start_edge
read -r s r _ <<<"$(scan "$OLD")";                      check "QR sinh trước lúc khởi động lại" "$s/$r" "REJECTED/REPLAY"
read -r s _ t _ <<<"$(scan "$(qr --did device_001)")"; check "QR mới sau khởi động lại" "$s/$t" "ACCEPTED/OPENED"
record "KB5 Turnstile timeout + Recovery" $f; pause

# ───────────────────────────── KB6 ─────────────────────────────
f=$FAIL; title "KB6 – Snapshot cũ hơn 4 giờ (mất đồng bộ khóa): chặn IN, cho OUT nhưng đánh dấu xem lại"
OLD_TS=$($PY -c 'from datetime import *;print((datetime.now(timezone.utc)-timedelta(hours=5)).isoformat())')
snapshot --device device_001 emp_001 --version 3 --issued-at "$OLD_TS"
read -r s r _ <<<"$(scan "$(qr --did device_001)" IN)";      check "Chiều IN" "$s/$r" "REJECTED/STALE_SNAPSHOT"
read -r s _ t n <<<"$(scan "$(qr --did device_001)" OUT)";   check "Chiều OUT" "$s/$t/needs_review=$n" "ACCEPTED/OPENED/needs_review=True"
record "KB6 Snapshot cũ" $f

# ───────────────────────────── Tổng kết ─────────────────────────────
title "Tổng kết"
for row in "${RESULTS[@]}"; do echo "  ${row##*|}   ${row%%|*}"; done
echo; echo "  Kiểm tra: ${c_ok}$PASS đạt${c_off}, $([[ $FAIL == 0 ]] && echo "${c_ok}0 lỗi${c_off}" || echo "${c_bad}$FAIL lỗi${c_off}")"
exit $(( FAIL > 0 ))

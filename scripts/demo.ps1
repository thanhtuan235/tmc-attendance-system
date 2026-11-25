<#
T2-05 – Demo 6 kịch bản CMP-01 Kiosk Control (Windows PowerShell 5.1 / PowerShell 7).
Script tự bật/tắt/khởi động lại Cloud (CMP-03 mini) và Edge (CMP-01), tự kiểm tra kết quả.

    .\scripts\demo.ps1            # dừng chờ Enter giữa các kịch bản (thuyết trình)
    .\scripts\demo.ps1 -NoPause   # chạy liền một mạch (kiểm thử nhanh)

Yêu cầu: chạy ở thư mục gốc repo, đã kích hoạt .venv và `pip install -e .[dev]`,
cổng 8000/8001 đang trống (tắt các cửa sổ uvicorn cũ).
Nếu bị chặn chạy script: Set-ExecutionPolicy -Scope Process RemoteSigned
#>
param([switch]$NoPause)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location (Split-Path $PSScriptRoot -Parent)

$Py    = (Get-Command python).Source
$Edge  = "http://127.0.0.1:8000"
$Cloud = "http://127.0.0.1:8001"
$Logs  = "demo-logs"
$env:TMC_SYNC_INTERVAL_SEC = "1"            # đồng bộ mỗi 1 giây cho demo nhanh
$env:TMC_CLOUD_EVENTS_URL  = "$Cloud/events"

$script:Pass = 0; $script:Fail = 0; $script:Results = @()
$script:EdgeProc = $null; $script:CloudProc = $null

# ───────────────────────────── Hàm tiện ích ─────────────────────────────
function Title($t) { Write-Host ""; Write-Host "══ $t ══" -ForegroundColor Cyan }
function Info($t)  { Write-Host "  $t" -ForegroundColor DarkGray }
function Pause-Demo { if (-not $NoPause) { [void](Read-Host "  ⏎ Enter để tiếp tục...") } }

function Check($desc, $actual, $expected) {
    if ("$actual" -eq "$expected") { Write-Host "  ✔ ${desc}: $actual" -ForegroundColor Green; $script:Pass++ }
    else { Write-Host "  ✘ ${desc}: nhận '$actual', mong đợi '$expected'" -ForegroundColor Red; $script:Fail++ }
}

function Record($name, $failBefore) {
    $script:Results += [pscustomobject]@{ "Kịch bản" = $name; "Kết quả" = $(if ($script:Fail -eq $failBefore) { "ĐẠT" } else { "LỖI" }) }
}

function Py { & $Py @args; if ($LASTEXITCODE -ne 0) { throw "Lệnh python $args lỗi" } }
function QR($did = "device_001") { (Py -m tools.fake_mobile --did $did | Out-String).Trim() }
function QR-Tampered { (Py -m tools.fake_mobile --did device_001 --tamper-sig | Out-String).Trim() }

function Snapshot { Py -m tools.fake_cmp02 @args | Out-Null; Invoke-RestMethod -Method Post "$Edge/admin/snapshot/reload" | Out-Null }
function Gate($mode) {
    Invoke-RestMethod -Method Put "$Edge/admin/turnstile-mode" -ContentType "application/json" `
        -Body (@{ mode = $mode } | ConvertTo-Json) | Out-Null
}
function Count   { (Invoke-RestMethod "$Cloud/events/count").count }
function Pending { (Invoke-RestMethod "$Edge/status").pending_sync }

function Scan($qr, $dir = "IN", $station = "BD-01") {
    $body = @{ station_id = $station; gate_id = "G1"; direction = $dir; qr = $qr } | ConvertTo-Json
    $r = Invoke-RestMethod -Method Post "$Edge/scan" -ContentType "application/json" -Body $body
    ($r | ConvertTo-Json -Compress) | Add-Content "$Logs\scan.jsonl"
    [pscustomobject]@{
        Status = $r.status
        Reason = $(if ($r.reason_code) { $r.reason_code } else { "-" })
        Tx     = $(if ($r.tx_status) { $r.tx_status } else { "-" })
        Review = $r.needs_review
    }
}

function Wait-Up($url) {
    for ($i = 0; $i -lt 75; $i++) {
        try { Invoke-RestMethod $url -TimeoutSec 1 | Out-Null; return } catch { Start-Sleep -Milliseconds 200 }
    }
    throw "Không bật được $url – xem log trong $Logs\"
}

function Start-Server($name, $target, $port, $healthUrl, [switch]$Factory) {
    $argList = @("-m", "uvicorn", $target, "--port", "$port")
    if ($Factory) { $argList += "--factory" }
    $p = Start-Process -FilePath $Py -ArgumentList $argList -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "$Logs\$name.out.log" -RedirectStandardError "$Logs\$name.log"
    Wait-Up $healthUrl
    return $p
}
function Start-Cloud { $script:CloudProc = Start-Server "cloud" "cloud.cmp03_attendance_mini.main:app" 8001 "$Cloud/events/count" }
function Start-Edge  { $script:EdgeProc  = Start-Server "edge" "edge.cmp01_kiosk_control.main:build_app" 8000 "$Edge/health" -Factory }
function Stop-Server($p) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force; $p.WaitForExit() } }

try {
    # ───────────────────────────── Chuẩn bị ─────────────────────────────
    Title "Chuẩn bị: xóa dữ liệu cũ, cấp khóa thiết bị, phát snapshot v1, bật Cloud + Edge"
    Remove-Item -Recurse -Force runtime-files, snapshot.json, cmp03_events.db, tools\devices, $Logs -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $Logs | Out-Null
    Py -m tools.fake_mobile --did device_001 --export-public-key | Out-Null
    Py -m tools.fake_mobile --did device_002 --export-public-key | Out-Null
    Py -m tools.fake_cmp02 --device device_001 emp_001 --device device_002 emp_002 --version 1 | Out-Null
    Start-Cloud; Start-Edge
    Info "Cloud: $Cloud/docs   Edge: $Edge/docs   Log: $Logs\"
    Pause-Demo

    # ───────────────────────────── KB1 ─────────────────────────────
    $f = $script:Fail; Title "KB1 – Quét QR hợp lệ → mở cổng, ghi nhận, đồng bộ lên Cloud"
    $q1 = QR
    $r = Scan $q1;                              Check "Kết quả quét" "$($r.Status)/$($r.Tx)" "ACCEPTED/OPENED"
    Start-Sleep 2;                              Check "Số bản ghi trên Cloud" (Count) 1
    Record "KB1 Quét hợp lệ" $f; Pause-Demo

    # ───────────────────────────── KB2 ─────────────────────────────
    $f = $script:Fail; Title "KB2 – Chống dùng lại QR (chụp màn hình đem quét ở cổng khác) và QR giả"
    $r = Scan $q1 "IN" "BD-02";                 Check "QR cũ ở cổng BD-02" "$($r.Status)/$($r.Reason)" "REJECTED/REPLAY"
    $r = Scan (QR-Tampered);                    Check "QR sai chữ ký" "$($r.Status)/$($r.Reason)" "REJECTED/BAD_SIGNATURE"
    Record "KB2 Replay + giả mạo" $f; Pause-Demo

    # ───────────────────────────── KB3 ─────────────────────────────
    $f = $script:Fail; Title "KB3 – Thu hồi thiết bị device_002 (snapshot v2) → bị từ chối ngay tại Edge"
    Snapshot --device device_001 emp_001 --device device_002 emp_002 --revoke-device device_002 --version 2
    $r = Scan (QR "device_002");                Check "device_002 (đã thu hồi)" "$($r.Status)/$($r.Reason)" "REJECTED/DEVICE_REVOKED"
    $r = Scan (QR "device_001");                Check "device_001 (bình thường)" "$($r.Status)/$($r.Tx)" "ACCEPTED/OPENED"
    Record "KB3 Thu hồi thiết bị" $f; Pause-Demo

    # ───────────────────────────── KB4 ─────────────────────────────
    $f = $script:Fail; Title "KB4 – Mất kết nối Cloud: cổng vẫn mở, bản ghi giữ trong outbox, có mạng thì đồng bộ đủ"
    Start-Sleep 2; $n = Count; Info "Cloud đang có $n bản ghi. Tắt Cloud..."
    Stop-Server $script:CloudProc
    foreach ($i in 1..3) { $r = Scan (QR); Check "Quét lần $i khi mất Cloud" "$($r.Status)/$($r.Tx)" "ACCEPTED/OPENED" }
    Start-Sleep 2;                              Check "Bản ghi chờ đồng bộ (pending_sync)" (Pending) 3
    Info "Bật lại Cloud..."; Start-Cloud
    for ($i = 0; $i -lt 30 -and (Pending) -ne 0; $i++) { Start-Sleep -Milliseconds 500 }
    Check "pending_sync sau khi có mạng" (Pending) 0
    Check "Cloud nhận đủ, không trùng" (Count) ($n + 3)
    Record "KB4 Mất Cloud → đồng bộ lại" $f; Pause-Demo

    # ───────────────────────────── KB5 ─────────────────────────────
    $f = $script:Fail; Title "KB5 – Turnstile không phản hồi → UNCONFIRMED; Edge sập và khởi động lại → Recovery"
    Gate "TIMEOUT"
    $r = Scan (QR);                             Check "Cổng không ACK" "$($r.Status)/$($r.Tx)" "ACCEPTED/UNCONFIRMED"
    Gate "OK"
    $old = QR; Info "Đã sinh QR trước khi Edge sập. Tắt cứng Edge rồi khởi động lại..."
    Stop-Server $script:EdgeProc; Start-Sleep 1; Start-Edge
    $r = Scan $old;                             Check "QR sinh trước lúc khởi động lại" "$($r.Status)/$($r.Reason)" "REJECTED/REPLAY"
    $r = Scan (QR);                             Check "QR mới sau khởi động lại" "$($r.Status)/$($r.Tx)" "ACCEPTED/OPENED"
    Record "KB5 Turnstile timeout + Recovery" $f; Pause-Demo

    # ───────────────────────────── KB6 ─────────────────────────────
    $f = $script:Fail; Title "KB6 – Snapshot cũ hơn 4 giờ (mất đồng bộ khóa): chặn IN, cho OUT nhưng đánh dấu xem lại"
    $oldTs = (Get-Date).ToUniversalTime().AddHours(-5).ToString("yyyy-MM-ddTHH:mm:ss+00:00")
    Snapshot --device device_001 emp_001 --version 3 --issued-at $oldTs
    $r = Scan (QR) "IN";                        Check "Chiều IN" "$($r.Status)/$($r.Reason)" "REJECTED/STALE_SNAPSHOT"
    $r = Scan (QR) "OUT";                       Check "Chiều OUT" "$($r.Status)/$($r.Tx)/needs_review=$($r.Review)" "ACCEPTED/OPENED/needs_review=True"
    Record "KB6 Snapshot cũ" $f

    # ───────────────────────────── Tổng kết ─────────────────────────────
    Title "Tổng kết"
    $script:Results | Format-Table -AutoSize | Out-String | Write-Host
    $color = if ($script:Fail -eq 0) { "Green" } else { "Red" }
    Write-Host "  Kiểm tra: $($script:Pass) đạt, $($script:Fail) lỗi" -ForegroundColor $color
}
finally {
    Stop-Server $script:EdgeProc
    Stop-Server $script:CloudProc
}
exit [int]($script:Fail -gt 0)

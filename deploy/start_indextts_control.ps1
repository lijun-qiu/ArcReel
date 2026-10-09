# Start IndexTTS host control on :10004 (lightweight).
# ArcReel Docker reaches it via http://host.docker.internal:10004
# Control can start/unload the speech proxy on :10003.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:INDEXTTS_CONTROL_PORT = if ($env:INDEXTTS_CONTROL_PORT) { $env:INDEXTTS_CONTROL_PORT } else { "10004" }
$env:APP_PORT = if ($env:APP_PORT) { $env:APP_PORT } else { "10003" }
$env:PROXY_API_KEY = if ($env:PROXY_API_KEY) { $env:PROXY_API_KEY } else { "indextts_proxy_api_key" }

$existing = Get-NetTCPConnection -LocalPort ([int]$env:INDEXTTS_CONTROL_PORT) -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1 -ExpandProperty OwningProcess
if ($existing) {
  Write-Host "IndexTTS control already on :$($env:INDEXTTS_CONTROL_PORT) (PID $existing)"
  exit 0
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
$log = Join-Path $Root "logs\indextts_control.log"
Write-Host "Starting IndexTTS host control on :$($env:INDEXTTS_CONTROL_PORT)"
# Redirect stdout+stderr via cmd so both can share one log file (Start-Process forbids it).
$arg = "/c `"python `"$Root\indextts_host_control.py`" >> `"$log`" 2>&1`""
Start-Process -FilePath "cmd.exe" -ArgumentList $arg -WorkingDirectory $Root -WindowStyle Hidden
Start-Sleep -Seconds 2
$owner = Get-NetTCPConnection -LocalPort ([int]$env:INDEXTTS_CONTROL_PORT) -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1 -ExpandProperty OwningProcess
if (-not $owner) {
  Start-Sleep -Seconds 2
  $owner = Get-NetTCPConnection -LocalPort ([int]$env:INDEXTTS_CONTROL_PORT) -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty OwningProcess
}
if ($owner) {
  Write-Host "OK: control PID $owner  (POST /start /unload, GET /status)"
} else {
  Write-Host "WARN: control did not bind yet — check $log"
  exit 1
}

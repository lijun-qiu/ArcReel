# Stop IndexTTS speech proxy (:10003) and unload CLI workers. Prefer host control when up.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ControlPort = if ($env:INDEXTTS_CONTROL_PORT) { [int]$env:INDEXTTS_CONTROL_PORT } else { 10004 }
$SpeechPort = if ($env:APP_PORT) { [int]$env:APP_PORT } else { 10003 }

try {
  $r = Invoke-RestMethod -Uri "http://127.0.0.1:$ControlPort/unload" -Method POST -TimeoutSec 20
  Write-Host ("unloaded via control: killed={0} running={1}" -f $r.killed, $r.running)
  exit 0
} catch {
  Write-Host "control not reachable — falling back to local kill"
}

$owner = Get-NetTCPConnection -LocalPort $SpeechPort -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1 -ExpandProperty OwningProcess
if ($owner) {
  Write-Host "Stopping speech proxy PID $owner on :$SpeechPort"
  Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
}

$killed = 0
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.Name -match 'python' -and
    $_.CommandLine -and
    ($_.CommandLine -match 'indextts\.cli_v2' -or $_.CommandLine -match 'indextts_speech_proxy')
  } |
  ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch {}
  }
Write-Host "Fallback unload done (extra killed=$killed)"

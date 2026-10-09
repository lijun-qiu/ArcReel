# Start IndexTTS OpenAI-compatible speech proxy on :10003 (host GPU).
# ArcReel Docker reaches it via http://host.docker.internal:10003

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:INDEX_TTS_ROOT = if ($env:INDEX_TTS_ROOT) { $env:INDEX_TTS_ROOT } else { "C:\my\index-tts\index-tts" }
$env:INDEX_TTS_PYTHON = if ($env:INDEX_TTS_PYTHON) { $env:INDEX_TTS_PYTHON } else { "C:\my\index-tts\index-tts\.venv\Scripts\python.exe" }
$env:INDEX_TTS_MODEL_DIR = if ($env:INDEX_TTS_MODEL_DIR) { $env:INDEX_TTS_MODEL_DIR } else { "C:\my\index-tts\index-tts\checkpoints" }
$env:INDEX_TTS_DEVICE = if ($env:INDEX_TTS_DEVICE) { $env:INDEX_TTS_DEVICE } else { "cuda" }
$env:PROXY_API_KEY = if ($env:PROXY_API_KEY) { $env:PROXY_API_KEY } else { "indextts_proxy_api_key" }
$env:APP_PORT = if ($env:APP_PORT) { $env:APP_PORT } else { "10003" }
$env:LOCALMINIDRAMA_ROOT = if ($env:LOCALMINIDRAMA_ROOT) {
  $env:LOCALMINIDRAMA_ROOT
} else {
  "C:\project\LocalMiniDrama-main\LocalMiniDrama-main\backend-node"
}
$env:GPT_SOVITS_VOICES_FILE = Join-Path $env:LOCALMINIDRAMA_ROOT "data\gptsovits\voices.json"
$env:GPT_SOVITS_REF_ROOT = Join-Path $env:LOCALMINIDRAMA_ROOT "data\gptsovits-refs"
$env:HF_ENDPOINT = if ($env:HF_ENDPOINT) { $env:HF_ENDPOINT } else { "https://hf-mirror.com" }

$existing = Get-NetTCPConnection -LocalPort ([int]$env:APP_PORT) -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1 -ExpandProperty OwningProcess
if ($existing) {
  Write-Host "Port $($env:APP_PORT) already in use by PID $existing — stopping it"
  Stop-Process -Id $existing -Force
  Start-Sleep -Seconds 1
}

# Prefer host control when available (UI / ArcReel buttons talk to :10004).
$controlPort = if ($env:INDEXTTS_CONTROL_PORT) { [int]$env:INDEXTTS_CONTROL_PORT } else { 10004 }
$controlUp = Get-NetTCPConnection -LocalPort $controlPort -State Listen -ErrorAction SilentlyContinue
if (-not $controlUp) {
  & "$Root\start_indextts_control.ps1"
}

Write-Host "IndexTTS root: $($env:INDEX_TTS_ROOT)"
Write-Host "Voices: $($env:GPT_SOVITS_VOICES_FILE)"
Write-Host "Starting speech proxy via control POST /start …"
try {
  $r = Invoke-RestMethod -Uri "http://127.0.0.1:$controlPort/start" -Method POST -TimeoutSec 60
  Write-Host ("ok={0} started={1} running={2}" -f $r.ok, $r.started, $r.running)
  if (-not $r.running) { exit 1 }
  exit 0
} catch {
  Write-Host "control /start failed — falling back to foreground proxy"
  Write-Host "Listening on 0.0.0.0:$($env:APP_PORT)"
  python "$Root\indextts_speech_proxy.py"
}

# Start Real-ESRGAN host proxy on :10005 (with supervisor).
# ArcReel Docker reaches it via http://host.docker.internal:10005
#
# Modes:
#   default     — start proxy if down, then exit
#   -Supervisor — keep proxy alive and honor logs/realesrgan_autostart.request
#                 (writes from ArcReel when click-超分 finds the proxy down)

param(
  [switch]$Supervisor
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$env:REALESRGAN_PROXY_PORT = if ($env:REALESRGAN_PROXY_PORT) { $env:REALESRGAN_PROXY_PORT } else { "10005" }
$env:REALESRGAN_ROOT = if ($env:REALESRGAN_ROOT) {
  $env:REALESRGAN_ROOT
} else {
  "C:\project\realesrgan-ncnn-vulkan-20220424-windows"
}
$env:PROJECTS_HOST_ROOT = if ($env:PROJECTS_HOST_ROOT) {
  $env:PROJECTS_HOST_ROOT
} else {
  (Join-Path $Root "projects")
}

$Port = [int]$env:REALESRGAN_PROXY_PORT
$Logs = Join-Path $Root "logs"
$Flag = Join-Path $Logs "realesrgan_autostart.request"
$LogFile = Join-Path $Logs "realesrgan_proxy.log"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Get-ProxyPid {
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($c) { return [int]$c.OwningProcess }
  return $null
}

function Start-Proxy {
  $owner = Get-ProxyPid
  if ($owner) {
    Write-Host "RealESRGAN proxy already on :$Port (PID $owner)"
    return $owner
  }
  Write-Host "Starting RealESRGAN proxy on :$Port"
  Write-Host "  REALESRGAN_ROOT=$($env:REALESRGAN_ROOT)"
  Write-Host "  PROJECTS_HOST_ROOT=$($env:PROJECTS_HOST_ROOT)"
  $arg = "/c `"python `"$Root\realesrgan_host_proxy.py`" >> `"$LogFile`" 2>&1`""
  Start-Process -FilePath "cmd.exe" -ArgumentList $arg -WorkingDirectory $Root -WindowStyle Hidden
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    $owner = Get-ProxyPid
    if ($owner) {
      Write-Host "OK: proxy PID $owner  (GET /health /progress, POST /upscale /ensure)"
      return $owner
    }
  }
  Write-Host "WARN: proxy did not bind yet — check $LogFile"
  return $null
}

function Clear-AutostartFlag {
  if (Test-Path $Flag) {
    Remove-Item -Force $Flag -ErrorAction SilentlyContinue
  }
}

$null = Start-Proxy
Clear-AutostartFlag

if (-not $Supervisor) {
  $owner = Get-ProxyPid
  if (-not $owner) { exit 1 }
  exit 0
}

Write-Host "Supervisor mode: watching $Flag (Ctrl+C to stop)"
while ($true) {
  Start-Sleep -Seconds 2
  $need = Test-Path $Flag
  $owner = Get-ProxyPid
  if ($need -or -not $owner) {
    if ($need) {
      Write-Host "$(Get-Date -Format o) autostart request — ensuring proxy"
      Clear-AutostartFlag
    } elseif (-not $owner) {
      Write-Host "$(Get-Date -Format o) proxy down — restarting"
    }
    $null = Start-Proxy
    Clear-AutostartFlag
  }
}

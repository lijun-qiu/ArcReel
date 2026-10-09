# Restart ArcReel local dev stack (Docker backend + host Vite)
# Prefer launching via restart-dev.cmd (cmd /k) so the console never auto-closes.
$ErrorActionPreference = "Continue"
$DeployDir = $PSScriptRoot
$LogDir = Join-Path $DeployDir "logs"
$ConsoleLog = Join-Path $LogDir "restart-dev-console.log"

function Write-ConsoleLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction SilentlyContinue | Out-Null
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $ConsoleLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host $Message
}

function Stop-HostViteOnPort([int]$Port) {
    # Only stop host Node/Vite listeners — never Docker Desktop / docker-proxy PIDs.
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        if (-not $procId -or $procId -le 0) { continue }
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $name = $proc.ProcessName
        if ($name -match '^(node|pnpm|vite)$') {
            Write-ConsoleLog "Stopping host $name PID $procId on port $Port"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        } else {
            Write-ConsoleLog "Skip PID $procId ($name) on port $Port (not host Vite)"
        }
    }
}

Write-ConsoleLog "=== restart-dev begin ==="
try {
    Write-ConsoleLog "==> Restart Docker services (backend only)"
    Set-Location $DeployDir
    docker compose stop frontend 2>$null | Out-Null
    docker compose up -d --force-recreate arcreel
    docker compose stop openrouter-strip modelscope-strip 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose recreate failed (exit $LASTEXITCODE)"
    }

    Write-ConsoleLog "==> Stop existing host Vite on :5173"
    Stop-HostViteOnPort 5173
    Start-Sleep -Seconds 1

    & (Join-Path $DeployDir "start-dev.ps1")
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "start-dev.ps1 failed (exit $LASTEXITCODE)"
    }
    Write-ConsoleLog "Restart sequence finished OK."
} catch {
    Write-ConsoleLog "ERROR: $($_.Exception.Message)"
    exit 1
} finally {
    Write-ConsoleLog "=== restart-dev end ==="
}

# ArcReel local dev: Docker backend + host Vite (http://localhost:5173)
# Host Vite runs in a separate PowerShell window (-NoExit) so the console stays open.
$ErrorActionPreference = "Continue"
$DeployDir = $PSScriptRoot
$RepoRoot = Split-Path $DeployDir -Parent
$FrontendDir = Join-Path $RepoRoot "frontend"
$LogDir = Join-Path $DeployDir "logs"
$ViteLog = Join-Path $LogDir "vite-dev.log"
$ConsoleLog = Join-Path $LogDir "start-dev-console.log"

function Write-ConsoleLog([string]$Message) {
    New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction SilentlyContinue | Out-Null
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $ConsoleLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    Write-Host $Message
}

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
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

Write-ConsoleLog "=== start-dev begin ==="
try {
    Write-ConsoleLog "==> Docker backend (deploy/) — skip Compose frontend (host Vite)"
    Set-Location $DeployDir
    docker compose stop frontend 2>$null | Out-Null
    docker compose up -d arcreel
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up -d failed (exit $LASTEXITCODE)"
    }

    Write-ConsoleLog "==> Wait for backend health"
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:1241/health" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $healthy) {
        Write-ConsoleLog "WARN: Backend health not ready yet; Vite will still start."
    } else {
        Write-ConsoleLog "Backend OK: http://localhost:1241"
    }

    $needVite = $false
    if (Test-PortListening 5173) {
        $owner = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty OwningProcess
        $ownerName = $null
        if ($owner) {
            $ownerName = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
        }
        if ($ownerName -match '^(node|pnpm|vite)$') {
            Write-ConsoleLog "==> Vite already on :5173 ($ownerName) — skip"
        } else {
            Write-ConsoleLog "==> Port 5173 held by $ownerName — stopping Compose frontend, then start host Vite"
            docker compose stop frontend 2>$null | Out-Null
            Start-Sleep -Seconds 1
            Stop-HostViteOnPort 5173
            Start-Sleep -Seconds 1
            if (Test-PortListening 5173) {
                throw "Port 5173 still in use after stopping Compose frontend. Free it and re-run."
            }
            $needVite = $true
        }
    } else {
        $needVite = $true
    }

    if ($needVite) {
        Write-ConsoleLog "==> Start Vite on http://localhost:5173 (separate PowerShell window)"
        $pnpm = Get-Command pnpm -ErrorAction SilentlyContinue
        if (-not $pnpm) {
            throw "pnpm not found on PATH. Install pnpm, then re-run this script."
        }
        $viteCmd = "`$Host.UI.RawUI.WindowTitle = 'ArcReel Vite'; Set-Location -LiteralPath '$FrontendDir'; Write-Host 'Vite: http://localhost:5173'; pnpm dev"
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoExit", "-NoLogo", "-ExecutionPolicy", "Bypass", "-Command", $viteCmd) `
            -WorkingDirectory $FrontendDir | Out-Null

        $viteReady = $false
        for ($i = 0; $i -lt 45; $i++) {
            if (Test-PortListening 5173) {
                $viteReady = $true
                break
            }
            Start-Sleep -Seconds 1
        }
        if (-not $viteReady) {
            Write-ConsoleLog "WARN: Vite did not open :5173 in time. Log: $ViteLog"
        } else {
            Write-ConsoleLog "Vite OK: http://localhost:5173"
        }
    }

    Write-ConsoleLog ""
    Write-ConsoleLog "==> IndexTTS host control (:10004)"
    try {
        & (Join-Path $DeployDir "start_indextts_control.ps1")
    } catch {
        Write-ConsoleLog "WARN: IndexTTS control not started: $($_.Exception.Message)"
    }

    Write-ConsoleLog ""
    Write-ConsoleLog "Dev URLs:"
    Write-ConsoleLog "  Frontend (HMR): http://localhost:5173/"
    Write-ConsoleLog "  Backend API:    http://localhost:1241/"
    Write-ConsoleLog "  IndexTTS ctrl:  http://localhost:10004/status"
    Write-ConsoleLog "Done."
} catch {
    Write-ConsoleLog "ERROR: $($_.Exception.Message)"
    exit 1
} finally {
    Write-ConsoleLog "=== start-dev end ==="
}

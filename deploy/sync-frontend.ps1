# 把本地前端构建产物同步进 Docker 挂载目录（frontend/dist）。
# 用法（在仓库根或 deploy 目录）：powershell -File deploy/sync-frontend.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "frontend")
Write-Host "Building frontend..."
pnpm build
Write-Host "Done. Hard-refresh http://localhost:1241 (Ctrl+F5) to load new UI."

@echo off
REM Default: start proxy once. Use "start_realesrgan_proxy.cmd -Supervisor" to keep it alive
REM and auto-start when ArcReel writes logs\realesrgan_autostart.request.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_realesrgan_proxy.ps1" %*
